"""
Scoop AI Function Calling Loop (v2.0)
=====================================

Encapsulates the multi-round function calling logic.

This module replaces the 400+ line manual loop in main.py (L2326-2663)
with a clean, testable implementation.

Key Features:
1. State isolated in LoopState (no scattered variables)
2. No Option D - empty rounds trigger retry, then fail explicitly
3. Single implementation used by both /chat and /chat/stream endpoints
4. Testable in isolation with mock dependencies

Error Handling Strategy:
- If texts=0 and has products -> retry ONCE with summary prompt
- If still texts=0 after retry -> raise EmptyResponseError
- Frontend handles error appropriately

Design Principle: Fail fast with one retry, no magic fallbacks.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Awaitable, Protocol

from google.genai.types import Part

from .types import FunctionCall, LoopState, RoundResult, RoundOutput
from .tool_executor import ToolExecutor, ToolResult

logger = logging.getLogger(__name__)


# =============================================================================
# EXCEPTIONS
# =============================================================================

class EmptyResponseError(Exception):
    """
    Raised when no text generated after retry attempt.

    This is a structured failure that the caller should handle
    by returning an appropriate error response to the user.
    """

    def __init__(
        self,
        message: str,
        rounds_completed: int = 0,
        products_found: int = 0,
        retry_attempted: bool = False,
    ):
        super().__init__(message)
        self.rounds_completed = rounds_completed
        self.products_found = products_found
        self.retry_attempted = retry_attempted


class LoopTimeoutError(Exception):
    """Raised when the loop times out."""
    pass


# =============================================================================
# PROTOCOL FOR CHAT SESSION
# =============================================================================

class ChatSessionProtocol(Protocol):
    """
    Protocol defining the interface for a chat session.

    This allows us to mock the Gemini SDK in tests.
    """

    async def send_message(self, message: Any) -> Any:
        """Send a message and get complete response."""
        ...

    async def send_message_stream(self, message: Any) -> Any:
        """Send a message and get streaming response."""
        ...


# =============================================================================
# FUNCTION CALLING LOOP
# =============================================================================

@dataclass
class LoopConfig:
    """Configuration for FunctionCallingLoop."""
    max_rounds: int = 3
    timeout_seconds: int = 30
    max_unique_queries: int = 3
    enable_retry: bool = True


class FunctionCallingLoop:
    """
    Encapsulates the multi-round function calling logic.

    REPLACES: The 400+ line manual loop in main.py (L2326-2663)

    KEY CHANGES FROM v1.0:
    1. State isolated in LoopState (no scattered variables)
    2. No Option D - empty rounds trigger retry, then fail explicitly
    3. Single implementation used by both endpoints
    4. Testable in isolation

    USAGE:
        executor = ToolExecutor(user_id="user123", ...)
        loop = FunctionCallingLoop(
            chat_session=gemini_chat,
            tool_executor=executor,
        )

        try:
            state = await loop.execute("მინდა პროტეინი")
            print(state.accumulated_text)
            print(state.all_products)
        except EmptyResponseError as e:
            # Handle empty response
            return error_response(e)
    """

    # Summary prompt template for retry
    SUMMARY_PROMPT_TEMPLATE = """
ნაპოვნია {product_count} პროდუქტი. გთხოვთ დაწეროთ მოკლე რეკომენდაცია ქართულად:

1. რატომ შეესაბამება ეს პროდუქტები მომხმარებლის მოთხოვნას
2. როგორ გამოიყენოს (დოზირება, დრო)
3. რა უნდა გაითვალისწინოს

აუცილებლად დაწერეთ ტექსტი, არა მხოლოდ პროდუქტების სია!
""".strip()

    def __init__(
        self,
        chat_session: ChatSessionProtocol,
        tool_executor: ToolExecutor,
        config: Optional[LoopConfig] = None,
        # Optional callback for streaming progress
        on_thought: Optional[Callable[[str], Awaitable[None]]] = None,
        on_text_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
        on_function_call: Optional[Callable[[FunctionCall], Awaitable[None]]] = None,
    ):
        """
        Initialize FunctionCallingLoop.

        Args:
            chat_session: Gemini chat session (or mock for testing)
            tool_executor: ToolExecutor instance with user context
            config: Loop configuration
            on_thought: Callback when thought part received (for streaming)
            on_text_chunk: Callback when text chunk received (for streaming)
            on_function_call: Callback when function call detected (for streaming)
        """
        self.session = chat_session
        self.executor = tool_executor
        self.config = config or LoopConfig()

        # Callbacks for streaming
        self._on_thought = on_thought
        self._on_text_chunk = on_text_chunk
        self._on_function_call = on_function_call

        # Initialize state
        self.state = LoopState()

    # =========================================================================
    # MAIN EXECUTION
    # =========================================================================

    async def execute(self, initial_message: str) -> LoopState:
        """
        Execute the function calling loop.

        This is the main entry point. It:
        1. Sends the initial message
        2. Processes responses (text, function calls, thoughts)
        3. Executes function calls and sends results back
        4. Repeats until complete or max rounds reached
        5. Handles retry on empty response

        Args:
            initial_message: The (possibly enhanced) user message

        Returns:
            Final LoopState with accumulated text and products

        Raises:
            EmptyResponseError: If no text after retry attempt
            LoopTimeoutError: If loop times out
        """
        current_message: Any = initial_message

        for round_num in range(self.config.max_rounds):
            logger.info(f"🔄 Loop round {round_num + 1}/{self.config.max_rounds}")

            try:
                # Execute single round with timeout
                output = await asyncio.wait_for(
                    self._execute_round(current_message),
                    timeout=self.config.timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.error(f"⏰ Round {round_num + 1} timed out after {self.config.timeout_seconds}s")
                raise LoopTimeoutError(
                    f"Timeout after {self.config.timeout_seconds}s in round {round_num + 1}"
                )

            # Update state from round output
            self._update_state_from_output(output)

            logger.info(
                f"📊 Round {round_num + 1} result: {output.result.value}, "
                f"text={len(output.text)}, fc={len(output.function_calls)}, "
                f"products={len(output.products_found)}"
            )

            # Decide next action based on result
            match output.result:

                case RoundResult.COMPLETE:
                    # Got text, we're done!
                    logger.info(f"✅ Loop complete: {len(self.state.accumulated_text)} chars text")
                    return self.state

                case RoundResult.CONTINUE:
                    # Got function calls, execute and continue
                    responses = await self._execute_function_calls(output.function_calls)
                    current_message = self._build_function_response_message(
                        output.function_calls,
                        responses,
                    )

                case RoundResult.EMPTY:
                    # No text, no function calls - problem state
                    if self._should_retry():
                        logger.info(f"🔄 Retrying with summary prompt (products: {len(self.state.all_products)})")
                        self.state.retry_attempted = True
                        current_message = self._build_summary_prompt()
                        continue
                    else:
                        # Already retried or no products - fail fast
                        raise EmptyResponseError(
                            f"No text generated after {round_num + 1} rounds",
                            rounds_completed=round_num + 1,
                            products_found=len(self.state.all_products),
                            retry_attempted=self.state.retry_attempted,
                        )

                case RoundResult.ERROR:
                    # Error occurred
                    raise RuntimeError(f"Round error: {output.error}")

        # Max rounds reached
        if not self.state.accumulated_text.strip():
            # Try one final retry if we have products and haven't retried
            if self._should_retry():
                logger.info("🔄 Final retry attempt after max rounds")
                self.state.retry_attempted = True
                output = await self._execute_round(self._build_summary_prompt())
                self._update_state_from_output(output)

            if not self.state.accumulated_text.strip():
                raise EmptyResponseError(
                    f"Max rounds ({self.config.max_rounds}) reached with no text",
                    rounds_completed=self.config.max_rounds,
                    products_found=len(self.state.all_products),
                    retry_attempted=self.state.retry_attempted,
                )

        return self.state

    # =========================================================================
    # ROUND EXECUTION
    # =========================================================================

    async def _execute_round(self, message: Any) -> RoundOutput:
        """
        Execute a single round of the loop.

        Sends message to Gemini and processes the response.

        Args:
            message: Message to send (string or function responses)

        Returns:
            RoundOutput with result type and extracted data
        """
        text_parts: List[str] = []
        function_calls: List[FunctionCall] = []
        thoughts: List[str] = []
        products: List[Dict[str, Any]] = []

        try:
            # Send message and get response
            response = await self.session.send_message(message)

            # Process response parts
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content:
                    # DEFENSIVE: Check parts is not None before iterating
                    parts = candidate.content.parts
                    if parts is not None:
                        for part in parts:
                            await self._process_part(
                                part,
                                text_parts,
                                function_calls,
                                thoughts,
                            )

            # Also check response.text as fallback
            if not text_parts and hasattr(response, 'text') and response.text:
                text_parts.append(response.text)

        except Exception as e:
            logger.error(f"Round execution error: {e}", exc_info=True)
            return RoundOutput(
                result=RoundResult.ERROR,
                error=str(e),
            )

        # Determine result type
        accumulated_text = "".join(text_parts)

        # Bug #27 Fix: FC ALWAYS takes priority over prelude text
        # When Gemini emits both text AND function call, the text is a "prelude"
        # (interrupted thought), not the final answer. Execute FC, discard prelude.
        if function_calls:
            result = RoundResult.CONTINUE
            if accumulated_text.strip():
                logger.info(
                    f"⚠️ Discarding prelude text ({len(accumulated_text)} chars) "
                    f"in favor of {len(function_calls)} function call(s)"
                )
                accumulated_text = ""  # Clear prelude
        elif accumulated_text.strip():
            result = RoundResult.COMPLETE
        else:
            result = RoundResult.EMPTY

        return RoundOutput(
            result=result,
            text=accumulated_text,
            function_calls=function_calls,
            products_found=products,
            thoughts=thoughts,
        )

    async def _process_part(
        self,
        part: Any,
        text_parts: List[str],
        function_calls: List[FunctionCall],
        thoughts: List[str],
    ) -> None:
        """
        Process a single part from Gemini response.

        Categorizes part as thought, function call, or text.

        Args:
            part: Part object from Gemini response
            text_parts: List to append text to
            function_calls: List to append function calls to
            thoughts: List to append thoughts to (for logging only)
        """
        # Check for thought part
        if hasattr(part, 'thought') and part.thought:
            thought_text = getattr(part, 'text', '') or ''
            if thought_text:
                thoughts.append(thought_text)
                # Invoke callback for streaming
                if self._on_thought:
                    await self._on_thought(thought_text)
            return

        # Check for function call
        fc = FunctionCall.from_sdk_part(part)
        if fc:
            function_calls.append(fc)
            if self._on_function_call:
                await self._on_function_call(fc)
            return

        # Check for text
        if hasattr(part, 'text') and part.text:
            text_parts.append(part.text)
            if self._on_text_chunk:
                await self._on_text_chunk(part.text)

    # =========================================================================
    # FUNCTION CALL EXECUTION
    # =========================================================================

    async def _execute_function_calls(
        self,
        calls: List[FunctionCall],
    ) -> List[ToolResult]:
        """
        Execute function calls via ToolExecutor.

        Uses batch execution with deduplication.

        Args:
            calls: List of function calls to execute

        Returns:
            List of ToolResults
        """
        # Filter to only first search_products (batch dedup)
        # This matches v1.0 behavior of only processing first search per batch
        filtered_calls = []
        search_seen = False

        for call in calls:
            if call.name == "search_products":
                if not search_seen:
                    filtered_calls.append(call)
                    search_seen = True
                else:
                    logger.warning(f"⚠️ Skipping duplicate search_products in batch")
            else:
                filtered_calls.append(call)

        logger.info(f"🔧 Executing {len(filtered_calls)} function calls (filtered from {len(calls)})")

        # Execute via ToolExecutor
        results = await self.executor.execute_batch(filtered_calls)

        # Track products from results
        for result in results:
            if result.products:
                added = self.state.add_products(result.products)
                logger.info(f"📦 Added {added} products from {result.name}")

        return results

    def _build_function_response_message(
        self,
        calls: List[FunctionCall],
        results: List[ToolResult],
    ) -> List[Part]:
        """
        Build function response message for next round.

        Creates SDK Part objects that Gemini expects for function responses.

        Args:
            calls: Original function calls
            results: Results from execution

        Returns:
            List of Part objects with function responses for Gemini
        """
        parts = []
        for call, result in zip(calls, results):
            parts.append(
                Part.from_function_response(
                    name=call.name,
                    response=result.response,
                )
            )

        return parts

    # =========================================================================
    # RETRY LOGIC
    # =========================================================================

    def _should_retry(self) -> bool:
        """
        Determine if we should retry after empty round.

        Conditions:
        1. Retry is enabled in config
        2. Products have been found
        3. Retry not already attempted

        Returns:
            True if retry should be attempted
        """
        return (
            self.config.enable_retry
            and len(self.state.all_products) > 0
            and not self.state.retry_attempted
        )

    def _build_summary_prompt(self) -> str:
        """
        Build the explicit summary request for retry.

        Uses template with product count.

        Returns:
            Summary prompt string
        """
        return self.SUMMARY_PROMPT_TEMPLATE.format(
            product_count=len(self.state.all_products)
        )

    # =========================================================================
    # STATE MANAGEMENT
    # =========================================================================

    def _update_state_from_output(self, output: RoundOutput) -> None:
        """
        Update loop state from round output.

        Accumulates text, products, and thoughts.

        Args:
            output: RoundOutput from completed round
        """
        self.state.rounds_completed += 1

        if output.text:
            self.state.accumulated_text += output.text

        if output.products_found:
            self.state.add_products(output.products_found)

        if output.thoughts:
            self.state.all_thoughts.extend(output.thoughts)

    def reset(self) -> None:
        """
        Reset loop state for reuse.

        Call this if you want to reuse the loop instance.
        """
        self.state = LoopState()

    # =========================================================================
    # STREAMING VARIANT
    # =========================================================================

    async def execute_streaming(
        self,
        initial_message: str,
    ) -> LoopState:
        """
        Execute loop with streaming support.

        Similar to execute() but uses send_message_stream for real-time chunks.
        Callbacks (on_thought, on_text_chunk) are invoked as data arrives.

        Args:
            initial_message: The user message

        Returns:
            Final LoopState

        Raises:
            EmptyResponseError: If no text after retry
        """
        current_message: Any = initial_message

        for round_num in range(self.config.max_rounds):
            logger.info(f"🔄 Streaming round {round_num + 1}/{self.config.max_rounds}")

            try:
                output = await asyncio.wait_for(
                    self._execute_round_streaming(current_message),
                    timeout=self.config.timeout_seconds,
                )
            except asyncio.TimeoutError:
                raise LoopTimeoutError(
                    f"Streaming timeout after {self.config.timeout_seconds}s"
                )

            self._update_state_from_output(output)

            match output.result:
                case RoundResult.COMPLETE:
                    return self.state

                case RoundResult.CONTINUE:
                    responses = await self._execute_function_calls(output.function_calls)
                    current_message = self._build_function_response_message(
                        output.function_calls,
                        responses,
                    )

                case RoundResult.EMPTY:
                    if self._should_retry():
                        self.state.retry_attempted = True
                        current_message = self._build_summary_prompt()
                        continue
                    else:
                        raise EmptyResponseError(
                            f"No text in streaming round {round_num + 1}",
                            rounds_completed=round_num + 1,
                            products_found=len(self.state.all_products),
                            retry_attempted=self.state.retry_attempted,
                        )

                case RoundResult.ERROR:
                    raise RuntimeError(f"Streaming error: {output.error}")

        if not self.state.accumulated_text.strip():
            raise EmptyResponseError(
                "Max streaming rounds with no text",
                rounds_completed=self.config.max_rounds,
                products_found=len(self.state.all_products),
                retry_attempted=self.state.retry_attempted,
            )

        return self.state

    async def _execute_round_streaming(self, message: Any) -> RoundOutput:
        """
        Execute a streaming round.

        Uses send_message_stream to get chunks as they arrive.

        Args:
            message: Message to send

        Returns:
            RoundOutput with accumulated results
        """
        text_parts: List[str] = []
        function_calls: List[FunctionCall] = []
        thoughts: List[str] = []

        try:
            # Get stream
            stream = await self.session.send_message_stream(message)

            # Process chunks as they arrive
            async for chunk in stream:
                if hasattr(chunk, 'candidates') and chunk.candidates:
                    candidate = chunk.candidates[0]
                    
                    # Log finish reason if present
                    if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
                        logger.debug(f"Chunk finish_reason: {candidate.finish_reason}")

                    if hasattr(candidate, 'content') and candidate.content:
                        # DEFENSIVE: Check parts is not None before iterating
                        parts = candidate.content.parts
                        if parts is not None:
                            for part in parts:
                                await self._process_part(
                                    part,
                                    text_parts,
                                    function_calls,
                                    thoughts,
                                )

        except Exception as e:
            logger.error(f"Streaming round error: {e}", exc_info=True)
            return RoundOutput(
                result=RoundResult.ERROR,
                error=str(e),
            )

        accumulated_text = "".join(text_parts)

        # Bug #27 Fix: FC ALWAYS takes priority over prelude text
        # When Gemini emits both text AND function call, the text is a "prelude"
        # (interrupted thought), not the final answer. Execute FC, discard prelude.
        if function_calls:
            result = RoundResult.CONTINUE
            if accumulated_text.strip():
                logger.info(
                    f"⚠️ Discarding prelude text ({len(accumulated_text)} chars) "
                    f"in favor of {len(function_calls)} function call(s)"
                )
                accumulated_text = ""  # Clear prelude
        elif accumulated_text.strip():
            result = RoundResult.COMPLETE
        else:
            result = RoundResult.EMPTY

        return RoundOutput(
            result=result,
            text=accumulated_text,
            function_calls=function_calls,
            thoughts=thoughts,
        )
