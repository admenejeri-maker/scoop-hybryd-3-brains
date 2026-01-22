"""
Scoop AI Conversation Engine (v2.0)
===================================

The unified conversation engine that replaces the dual-architecture of v1.0.

This is the main entry point for processing user messages. It orchestrates:
1. Context loading (history, user profile)
2. Function calling loop execution
3. Response assembly and delivery
4. Context persistence

Key Architectural Decisions:
1. Single implementation for both /chat and /chat/stream
2. No thought-as-text fallbacks (Option D eliminated)
3. Explicit parameter passing (no ContextVar magic)
4. Fail-fast with one retry on empty responses

Design Principle: One engine, two modes (sync vs stream).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .types import (
    ResponseMode,
    RequestContext,
    ConversationResult,
    EngineConfig,
    ErrorResponse,
    get_error_response,
    FunctionCall,
)
from .response_buffer import ResponseBuffer
from .tool_executor import ToolExecutor
from .function_loop import (
    FunctionCallingLoop,
    LoopConfig,
    EmptyResponseError,
    LoopTimeoutError,
)
from .thinking_manager import (
    ThinkingManager,
    ThinkingStrategy,
    ThinkingEvent,
    create_thinking_manager,
)

from app.adapters.gemini_adapter import GeminiAdapter, GeminiConfig
from app.adapters.mongo_adapter import MongoAdapter, MongoConfig

logger = logging.getLogger(__name__)


# =============================================================================
# SSE EVENT TYPES
# =============================================================================

@dataclass
class SSEEvent:
    """Server-Sent Event for streaming responses."""
    event_type: str
    data: Dict[str, Any]

    def to_sse(self) -> str:
        """Format as SSE string.
        
        Note: We include 'type' in data payload because frontend (Chat.tsx)
        parses JSON and checks data.type, not the SSE event: header.
        """
        import json
        # Merge type into data for frontend compatibility
        payload = {"type": self.event_type, **self.data}
        return f"event: {self.event_type}\ndata: {json.dumps(payload)}\n\n"


# =============================================================================
# ENGINE CONFIGURATION
# =============================================================================

@dataclass
class ConversationEngineConfig:
    """
    Configuration for ConversationEngine.

    Combines settings for all components.
    """
    # Engine mode
    default_mode: ResponseMode = ResponseMode.SYNC

    # Function calling settings
    max_function_rounds: int = 5  # Increased from 3 to prevent EmptyResponseError on complex queries
    max_unique_queries: int = 3

    # Timeout settings
    gemini_timeout_seconds: int = 30
    request_timeout_seconds: int = 60

    # Retry settings
    retry_on_empty: bool = True

    # Output settings
    max_output_tokens: int = 8192
    temperature: float = 0.7

    # Model settings
    model_name: str = "gemini-3-flash-preview"

    # Thinking UI settings
    # SIMPLE_LOADER is default - provides UX feedback without SDK bug #4090 issues
    thinking_strategy: str = "simple_loader"  # "none", "simple_loader", "native"
    thinking_delay_seconds: float = 0.3  # Delay between thinking steps for animation


# =============================================================================
# CONVERSATION ENGINE
# =============================================================================

class ConversationEngine:
    """
    Unified Conversation Engine for Scoop AI (v2.0).

    REPLACES:
    - The 737-line chat_stream function in main.py
    - The dual /chat vs /chat/stream behavior
    - Option D thought-as-text fallback
    - ContextVar-based user_id passing

    ARCHITECTURE:
    ```
    User Request
        │
        ▼
    ┌─────────────────────┐
    │ ConversationEngine  │ ◀── Entry point
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │    MongoAdapter     │ ◀── Load history, profile
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │   GeminiAdapter     │ ◀── Create chat session
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │ FunctionCallingLoop │ ◀── Multi-round execution
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │   ResponseBuffer    │ ◀── Assemble response
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │    MongoAdapter     │ ◀── Save history
    └─────────────────────┘
    ```

    USAGE:
        engine = ConversationEngine(
            gemini_api_key="...",
            mongo_uri="...",
        )

        # Sync mode
        result = await engine.process_message(
            user_id="user123",
            message="რომელი პროტეინი არის საუკეთესო?",
            mode=ResponseMode.SYNC,
        )

        # Stream mode
        async for event in engine.stream_message(
            user_id="user123",
            message="რომელი პროტეინი არის საუკეთესო?",
        ):
            yield event.to_sse()
    """

    def __init__(
        self,
        gemini_adapter: Optional[GeminiAdapter] = None,
        mongo_adapter: Optional[MongoAdapter] = None,
        config: Optional[ConversationEngineConfig] = None,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        # For direct initialization without adapters
        gemini_api_key: Optional[str] = None,
        mongo_uri: Optional[str] = None,
        mongo_database: Optional[str] = None,
    ):
        """
        Initialize ConversationEngine.

        Can be initialized with adapters (for testing) or API keys (for production).

        Args:
            gemini_adapter: Pre-configured GeminiAdapter
            mongo_adapter: Pre-configured MongoAdapter
            config: Engine configuration
            system_instruction: System prompt for Gemini
            tools: List of Gemini tool definitions
            gemini_api_key: Gemini API key (if not using adapter)
            mongo_uri: MongoDB URI (if not using adapter)
            mongo_database: MongoDB database name
        """
        self.config = config or ConversationEngineConfig()

        # Initialize adapters
        if gemini_adapter:
            self.gemini = gemini_adapter
        elif gemini_api_key:
            gemini_config = GeminiConfig(
                model_name=self.config.model_name,
                timeout_seconds=self.config.gemini_timeout_seconds,
                max_output_tokens=self.config.max_output_tokens,
                temperature=self.config.temperature,
            )
            self.gemini = GeminiAdapter(api_key=gemini_api_key, config=gemini_config)
        else:
            raise ValueError("Either gemini_adapter or gemini_api_key required")

        if mongo_adapter:
            self.mongo = mongo_adapter
        else:
            self.mongo = MongoAdapter()

        # Store tools and system instruction
        self.system_instruction = system_instruction
        self.tools = tools or []

        logger.info(
            f"ConversationEngine initialized: model={self.config.model_name}, "
            f"max_rounds={self.config.max_function_rounds}"
        )

    # =========================================================================
    # MAIN ENTRY POINTS
    # =========================================================================

    async def process_message(
        self,
        user_id: str,
        message: str,
        session_id: Optional[str] = None,
        mode: Optional[ResponseMode] = None,
    ) -> ConversationResult:
        """
        Process a message and return complete result.

        This is the main entry point for sync mode.

        Args:
            user_id: User identifier
            message: User message
            session_id: Optional session identifier
            mode: Response mode (defaults to SYNC)

        Returns:
            ConversationResult with text, products, tip, etc.
        """
        mode = mode or self.config.default_mode

        # Create request context
        context = RequestContext(
            user_id=user_id,
            message=message,
            session_id=session_id,
            mode=mode,
        )

        try:
            # Execute pipeline
            return await self._execute_pipeline(context)

        except EmptyResponseError as e:
            logger.error(f"Empty response: {e}")
            return get_error_response("empty_response").to_conversation_result()

        except LoopTimeoutError as e:
            logger.error(f"Timeout: {e}")
            return get_error_response("timeout").to_conversation_result()

        except Exception as e:
            logger.error(f"Engine error: {e}", exc_info=True)
            return get_error_response("internal_error").to_conversation_result()

    async def stream_message(
        self,
        user_id: str,
        message: str,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[SSEEvent]:
        """
        Process a message and yield SSE events.

        This is the main entry point for stream mode.

        Flow:
        1. Yield initial thinking events (immediate UX feedback)
        2. Load context (history, profile)
        3. Create chat session
        4. Execute function calling loop
        5. Yield final results (text, products, tip, quick_replies)
        6. Save history

        Args:
            user_id: User identifier
            message: User message
            session_id: Optional session identifier

        Yields:
            SSEEvent objects for real-time UI updates
        """
        # Create request context
        context = RequestContext(
            user_id=user_id,
            message=message,
            session_id=session_id,
            mode=ResponseMode.STREAM,
        )

        buffer = ResponseBuffer()

        # Create ThinkingManager for UX feedback
        thinking_manager = create_thinking_manager(
            strategy=self.config.thinking_strategy,
        )

        # CRITICAL: Track chat session for guaranteed save in finally block
        chat = None
        save_attempted = False

        try:
            # Phase 1: Yield initial thinking events (immediate feedback)
            for event in thinking_manager.get_initial_events(message):
                yield SSEEvent("thinking", event.to_sse_data())
                if self.config.thinking_delay_seconds > 0:
                    await asyncio.sleep(self.config.thinking_delay_seconds)

            # Phase 2: Load context
            await self._load_context(context)

            # Phase 3: Create chat session
            chat = await self._create_chat_session(context)

            # Phase 4: Create tool executor
            executor = await self._create_tool_executor(context)

            # Phase 5: Execute function calling loop with streaming
            # Create callbacks that yield SSE events
            async def on_function_call(fc: FunctionCall):
                """Callback when function call detected."""
                event = thinking_manager.get_function_call_event(fc.name)
                if event:
                    # Note: Can't yield from nested async - store for later
                    pass
                logger.debug(f"Function call: {fc.name}")

            loop = FunctionCallingLoop(
                chat_session=chat,
                tool_executor=executor,
                config=LoopConfig(
                    max_rounds=self.config.max_function_rounds,
                    timeout_seconds=self.config.gemini_timeout_seconds,
                    max_unique_queries=self.config.max_unique_queries,
                    enable_retry=self.config.retry_on_empty,
                ),
                on_text_chunk=lambda chunk: self._on_text_chunk(chunk, buffer),
                on_function_call=on_function_call,
            )

            # Enhanced message with context
            enhanced_message = self._enhance_message(context)

            # Execute streaming loop
            state = await loop.execute_streaming(enhanced_message)

            # Check if retry was attempted (texts=0 scenario)
            if state.retry_attempted:
                retry_event = thinking_manager.get_retry_event(len(state.all_products))
                yield SSEEvent("thinking", retry_event.to_sse_data())

            # Set buffer state from loop results
            buffer.set_text(state.accumulated_text)
            buffer.add_products(state.all_products)

            # Phase 6: Extract tip and quick replies
            buffer.extract_and_set_tip()
            buffer.parse_quick_replies()

            # Phase 7: Yield completion thinking event
            completion_event = thinking_manager.get_completion_event()
            if completion_event:
                yield SSEEvent("thinking", completion_event.to_sse_data())

            # Phase 8: Yield final results
            snapshot = buffer.snapshot()

            # Yield text
            yield SSEEvent("text", {"content": snapshot.text})

            # Yield products
            if snapshot.products:
                yield SSEEvent("products", {"products": snapshot.products})

            # Yield tip
            if snapshot.tip:
                yield SSEEvent("tip", {"content": snapshot.tip})

            # Yield quick replies
            if snapshot.quick_replies:
                yield SSEEvent("quick_replies", {"replies": snapshot.quick_replies})

            # Phase 9: Save history (will also be attempted in finally as backup)
            save_attempted = True
            await self._save_context(context, chat)

            # Done - include session_id so frontend can persist it for subsequent requests
            yield SSEEvent("done", {
                "success": True,
                "session_id": context.session_id,  # CRITICAL: Frontend needs this for session persistence
                "elapsed_seconds": context.elapsed_seconds(),
                "thinking_steps": thinking_manager.step_count,
            })

        except EmptyResponseError as e:
            logger.error(f"Empty response in stream: {e}")
            error = get_error_response("empty_response")
            yield SSEEvent("error", {
                "code": error.error_code,
                "message": error.message_georgian,
                "can_retry": error.can_retry,
            })

        except LoopTimeoutError as e:
            logger.error(f"Timeout in stream: {e}")
            error = get_error_response("timeout")
            yield SSEEvent("error", {
                "code": error.error_code,
                "message": error.message_georgian,
                "can_retry": error.can_retry,
            })

        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            error = get_error_response("internal_error")
            yield SSEEvent("error", {
                "code": error.error_code,
                "message": error.message_georgian,
                "can_retry": error.can_retry,
            })

        finally:
            # CRITICAL FIX: Guarantee history is saved even if an error occurred
            # This prevents "amnesia" where history is lost on partial failures
            if chat is not None and not save_attempted:
                logger.warning(
                    f"💾 FINALLY: Attempting backup save for session {context.session_id} "
                    f"(save_attempted={save_attempted})"
                )
                try:
                    await self._save_context(context, chat)
                except Exception as save_error:
                    logger.error(
                        f"💾 FINALLY: Backup save failed for session {context.session_id}: {save_error}"
                    )

    # =========================================================================
    # PIPELINE EXECUTION
    # =========================================================================

    async def _execute_pipeline(self, context: RequestContext) -> ConversationResult:
        """
        Execute the full processing pipeline.

        Steps:
        1. Load context (history, profile)
        2. Create chat session
        3. Execute function calling loop
        4. Build response
        5. Save context

        Args:
            context: RequestContext with all request data

        Returns:
            ConversationResult
        """
        # Step 1: Load context
        await self._load_context(context)

        # Step 2: Create chat session
        chat = await self._create_chat_session(context)

        # Step 3: Create tool executor
        executor = await self._create_tool_executor(context)

        # Step 4: Create and execute function calling loop
        loop = FunctionCallingLoop(
            chat_session=chat,
            tool_executor=executor,
            config=LoopConfig(
                max_rounds=self.config.max_function_rounds,
                timeout_seconds=self.config.gemini_timeout_seconds,
                max_unique_queries=self.config.max_unique_queries,
                enable_retry=self.config.retry_on_empty,
            ),
        )

        # Enhanced message with context
        enhanced_message = self._enhance_message(context)

        # Execute loop
        state = await loop.execute(enhanced_message)

        # Step 5: Build response
        buffer = ResponseBuffer()
        buffer.set_text(state.accumulated_text)
        buffer.add_products(state.all_products)

        # Extract tip and quick replies
        clean_text, tip, quick_replies = buffer.finalize()

        # Step 6: Save context
        await self._save_context(context, chat)

        # Build result
        return ConversationResult(
            text=clean_text,
            products=buffer.get_products(),
            tip=tip,
            quick_replies=quick_replies,
            success=True,
            metadata={
                "rounds": state.rounds_completed,
                "products_count": len(state.all_products),
                "elapsed_seconds": context.elapsed_seconds(),
            },
        )

    # =========================================================================
    # CONTEXT MANAGEMENT
    # =========================================================================

    async def _load_context(self, context: RequestContext) -> None:
        """
        Load history and user profile into context.

        Args:
            context: RequestContext to populate
        """
        # Load history
        logger.info(
            f"📥 _load_context START: user={context.user_id}, "
            f"requested_session={context.session_id}"
        )

        history, session_id, summary = await self.mongo.load_history(
            user_id=context.user_id,
            session_id=context.session_id,
        )

        context.history = history
        context.session_id = session_id

        # Load user profile
        profile = await self.mongo.get_user_profile(context.user_id)
        context.user_profile = profile or {}

        # CRITICAL LOG: Shows exactly what was loaded
        logger.info(
            f"📥 _load_context COMPLETE: user={context.user_id}, "
            f"session={context.session_id}, "
            f"history_len={len(context.history)}, "
            f"has_profile={profile is not None}, "
            f"has_summary={summary is not None}"
        )

        # DEBUG: Log first message if history exists (helps diagnose amnesia)
        if context.history:
            first_msg = context.history[0]
            first_text = first_msg.get('parts', [{}])[0].get('text', '')[:100]
            logger.debug(f"📥 First history message: {first_text}...")

    async def _save_context(self, context: RequestContext, chat: Any) -> None:
        """
        Save updated history after conversation.

        Args:
            context: RequestContext with session info
            chat: Gemini chat session with history
        """
        logger.info(
            f"💾 _save_context START: user={context.user_id}, "
            f"session={context.session_id}"
        )

        try:
            # Get history from chat session
            history = chat.get_history() if hasattr(chat, 'get_history') else []
            history_len = len(history) if history else 0

            logger.info(
                f"💾 Saving {history_len} messages to MongoDB for session {context.session_id}"
            )

            # Save to MongoDB
            save_success = await self.mongo.save_history(
                user_id=context.user_id,
                session_id=context.session_id,
                history=history,
            )

            if save_success:
                logger.info(
                    f"💾 _save_context SUCCESS: session={context.session_id}, "
                    f"messages_saved={history_len}"
                )
            else:
                logger.error(
                    f"💾 _save_context FAILED (save_history returned False): "
                    f"session={context.session_id}"
                )

            # Increment user stats
            await self.mongo.increment_user_stats(
                user_id=context.user_id,
                messages=2,  # User message + assistant response
            )

        except Exception as e:
            logger.error(
                f"💾 _save_context EXCEPTION: session={context.session_id}, error={e}",
                exc_info=True
            )
            # Don't fail the request if save fails

    # =========================================================================
    # CHAT SESSION CREATION
    # =========================================================================

    async def _create_chat_session(self, context: RequestContext) -> Any:
        """
        Create Gemini chat session with loaded context.

        Args:
            context: RequestContext with history and profile

        Returns:
            Gemini AsyncChat session
        """
        # Convert BSON history to SDK format
        sdk_history = self.gemini.bson_to_sdk_history(context.history)

        # Build system instruction with user profile
        system_instruction = self._build_system_instruction(context)

        # Create chat session (AFC disabled by GeminiAdapter)
        chat = self.gemini.create_chat(
            history=sdk_history,
            tools=self.tools,
            system_instruction=system_instruction,
        )

        return chat

    def _build_system_instruction(self, context: RequestContext) -> str:
        """
        Build system instruction with user profile context.

        Args:
            context: RequestContext with user profile

        Returns:
            Complete system instruction string
        """
        base_instruction = self.system_instruction or ""

        # Add user profile context if available
        if context.user_profile:
            profile_context = self._format_profile_context(context.user_profile)
            if profile_context:
                return f"{base_instruction}\n\n{profile_context}"

        return base_instruction

    def _format_profile_context(self, profile: Dict[str, Any]) -> str:
        """
        Format user profile for system prompt injection.

        Args:
            profile: User profile dict

        Returns:
            Formatted profile string for system prompt
        """
        parts = []

        if profile.get("name"):
            parts.append(f"მომხმარებლის სახელი: {profile['name']}")

        if profile.get("allergies"):
            parts.append(f"ალერგიები: {', '.join(profile['allergies'])}")

        if profile.get("goals"):
            parts.append(f"მიზნები: {', '.join(profile['goals'])}")

        if profile.get("fitness_level"):
            parts.append(f"ფიტნეს დონე: {profile['fitness_level']}")

        # Demographics
        demographics = profile.get("demographics", {})
        if demographics.get("age"):
            parts.append(f"ასაკი: {demographics['age']}")
        if demographics.get("gender"):
            parts.append(f"სქესი: {demographics['gender']}")

        # Physical stats
        physical = profile.get("physical_stats", {})
        if physical.get("height"):
            parts.append(f"სიმაღლე: {physical['height']} სმ")
        if physical.get("current_weight"):
            parts.append(f"წონა: {physical['current_weight']} კგ")

        if parts:
            return "მომხმარებლის პროფილი:\n" + "\n".join(parts)

        return ""

    # =========================================================================
    # TOOL EXECUTOR CREATION
    # =========================================================================

    async def _create_tool_executor(self, context: RequestContext) -> ToolExecutor:
        """
        Create ToolExecutor with explicit context.

        Args:
            context: RequestContext with user info

        Returns:
            Configured ToolExecutor
        """
        return ToolExecutor.create_with_defaults(
            user_id=context.user_id,
            user_profile=context.user_profile,
        )

    # =========================================================================
    # MESSAGE ENHANCEMENT
    # =========================================================================

    def _enhance_message(self, context: RequestContext) -> str:
        """
        Enhance user message with relevant context.

        Can add profile hints, session context, etc.

        Args:
            context: RequestContext

        Returns:
            Enhanced message string
        """
        # For now, just return the original message
        # Future enhancements could add:
        # - Relevant facts from semantic memory
        # - Previous conversation summary
        # - Time-based context
        return context.message

    # =========================================================================
    # STREAMING CALLBACKS
    # =========================================================================

    async def _on_text_chunk(self, chunk: str, buffer: ResponseBuffer) -> None:
        """
        Callback when text chunk received in streaming mode.

        Args:
            chunk: Text chunk from Gemini
            buffer: ResponseBuffer to accumulate into
        """
        buffer.append_text(chunk)

    async def _on_function_call(self, fc: Any) -> None:
        """
        Callback when function call detected in streaming mode.

        Args:
            fc: FunctionCall object
        """
        logger.debug(f"Function call detected: {fc.name}")


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_conversation_engine(
    gemini_api_key: Optional[str] = None,
    system_instruction: Optional[str] = None,
    tools: Optional[List[Any]] = None,
    config: Optional[ConversationEngineConfig] = None,
) -> ConversationEngine:
    """
    Factory function to create ConversationEngine with settings.

    Args:
        gemini_api_key: Gemini API key (falls back to settings)
        system_instruction: System prompt
        tools: Tool definitions
        config: Engine configuration

    Returns:
        Configured ConversationEngine
    """
    from config import settings

    return ConversationEngine(
        gemini_api_key=gemini_api_key or settings.gemini_api_key,
        system_instruction=system_instruction,
        tools=tools,
        config=config or ConversationEngineConfig(
            model_name=settings.model_name,
            max_output_tokens=settings.max_output_tokens,
            gemini_timeout_seconds=settings.gemini_timeout_seconds,
        ),
    )
