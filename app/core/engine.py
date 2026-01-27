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
from .hybrid_manager import HybridInferenceManager, HybridConfig
from .fallback_trigger import FallbackTrigger

from app.adapters.gemini_adapter import GeminiAdapter, GeminiConfig
from app.adapters.mongo_adapter import MongoAdapter, MongoConfig

logger = logging.getLogger(__name__)


# =============================================================================
# SEARCH-FIRST ARCHITECTURE CONSTANTS
# =============================================================================

# Product category keywords (Georgian + English)
PRODUCT_KEYWORDS = [
    # Proteins
    "პროტეინ", "ვეი", "იზოლატ", "კაზეინ", "ცილა", "whey", "protein",
    # Creatine
    "კრეატინ", "creatine",
    # Vitamins
    "ვიტამინ", "მინერალ", "ომეგა", "მაგნიუმ", "თუთია", "vitamin", "omega",
    # Amino acids
    "ამინო", "bcaa", "eaa",
    # Pre-workout
    "პრევორკაუთ", "პრე-ვორკაუთ", "preworkout",
    # Gainers
    "გეინერ", "gainer", "მასა",
    # General
    "დანამატ", "სპორტულ", "supplement",
]

# Intent verbs (Georgian present/future tense)
INTENT_VERBS = [
    "მინდა",      # I want
    "მჭირდება",   # I need
    "ვეძებ",      # I'm looking for
    "მირჩიე",     # recommend me
    "შემირჩიე",   # choose for me
    "რა გაქვთ",   # what do you have
    "რა არის",    # what is there
    "რომელი",     # which one
    "საუკეთესო",  # best
    "რეკომენდაცია", # recommendation
]

# Negative filters (past tense, complaints) - reject injection
NEGATIVE_MARKERS = [
    # Past tense markers
    "ვიყიდე",     # I bought
    "ვცადე",      # I tried
    "გამოვიყენე", # I used
    "მქონდა",     # I had
    "იყო",        # was
    "ვხმარობდი",  # I was using
    # Complaint markers
    "ცუდი",       # bad
    "არ მომეწონა", # I didn't like
    "პრობლემა",   # problem
    "გაფუჭ",      # spoiled/broken
    "დაბრუნება",  # return (refund)
    "რეკლამაცია", # complaint
]

# Framing template - tells Gemini this is reference data, not recommendation
INJECTION_TEMPLATE = """[კონტექსტი: Scoop.ge კატალოგი - {count} პროდუქტი ნაპოვნია]
{products}
[შენიშვნა: ეს არის კატალოგის მონაცემები. მომხმარებლის კითხვას უპასუხე ამ ინფორმაციის გამოყენებით.]

მომხმარებლის შეკითხვა: {message}"""


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
        import logging
        # Merge type into data for frontend compatibility
        payload = {"type": self.event_type, **self.data}
        json_str = json.dumps(payload, ensure_ascii=False)  # Keep UTF-8
        sse_str = f"event: {self.event_type}\ndata: {json_str}\n\n"
        # DEBUG: Log SSE output for text events
        if self.event_type == "text":
            content = self.data.get("content", "")
            logging.info(f"📡 SSE TEXT: len={len(content)}, json_len={len(json_str)}, sse_len={len(sse_str)}")
            logging.info(f"📡 SSE TEXT preview: {content[:100]}...")
        return sse_str


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
        
        # Initialize hybrid inference manager for model routing
        try:
            self.hybrid_manager = HybridInferenceManager()
            logger.info(
                f"HybridInferenceManager ready: "
                f"primary={self.hybrid_manager.config.primary_model}"
            )
        except Exception as e:
            logger.warning(f"HybridInferenceManager init failed: {e}")
            self.hybrid_manager = None

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

            # Phase 3: Route request using hybrid manager (if available)
            selected_model = None
            if self.hybrid_manager:
                try:
                    routing = self.hybrid_manager.route_request(
                        message=message,
                        history=context.history,
                    )
                    selected_model = routing.model
                    logger.info(
                        f"HybridRouter: selected {selected_model} "
                        f"(reason={routing.reason})"
                    )
                except Exception as e:
                    logger.warning(f"HybridRouter failed: {e}, using default model")
                    selected_model = None

            # Phase 4: Create chat session with routed model
            chat = await self._create_chat_session(context, model_override=selected_model)

            # Phase 5: Create tool executor
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
            
            # DEBUG: Log state for SAFETY analysis
            logger.info(
                f"🔬 DEBUG SAFETY CHECK: "
                f"last_finish_reason={state.last_finish_reason}, "
                f"text_len={len(state.accumulated_text)}, "
                f"text_stripped_len={len(state.accumulated_text.strip())}"
            )
            
            # SAFETY Fallback: Check if stream was cut due to SAFETY filter
            # Only retry if: (1) SAFETY detected, (2) minimal text generated, (3) not already retried
            safety_retry_attempted = False
            if (
                state.last_finish_reason 
                and "SAFETY" in state.last_finish_reason.upper()
                and len(state.accumulated_text.strip()) < 300  # Raised from 100 - Georgian greetings are ~130 chars
            ):
                logger.warning(
                    f"⚠️ SAFETY detected with only {len(state.accumulated_text)} chars, "
                    f"attempting fallback retry..."
                )
                
                # Record failure for circuit breaker
                if self.hybrid_manager and selected_model:
                    self.hybrid_manager.record_failure(
                        exception=RuntimeError("SAFETY_BLOCK")
                    )
                
                # Get fallback model
                if self.hybrid_manager:
                    fallback_model = self.hybrid_manager.get_fallback_model(selected_model)
                    if fallback_model and fallback_model != selected_model:
                        logger.info(f"🔄 Retrying with fallback model: {fallback_model}")
                        safety_retry_attempted = True
                        selected_model = fallback_model
                        
                        # Re-create chat session with fallback model
                        chat = await self._create_chat_session(context, model_override=fallback_model)
                        
                        # Re-create loop with new session
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
                        
                        # Clear buffer for fresh start
                        buffer.clear()
                        
                        # Re-execute streaming loop with fallback model
                        state = await loop.execute_streaming(enhanced_message)
                        logger.info(
                            f"✅ Fallback complete: {len(state.accumulated_text)} chars, "
                            f"finish_reason={state.last_finish_reason}"
                        )
                    else:
                        logger.warning("No fallback model available, returning partial response")

            # Check if retry was attempted (texts=0 scenario)
            if state.retry_attempted or safety_retry_attempted:
                retry_event = thinking_manager.get_retry_event(len(state.all_products))
                yield SSEEvent("thinking", retry_event.to_sse_data())

            # Set buffer state from loop results
            buffer.set_text(state.accumulated_text)
            # DEBUG: Log products before adding to buffer
            logger.info(f"📊 DEBUG: state.all_products has {len(state.all_products)} products")
            buffer.add_products(state.all_products)
            logger.info(f"📊 DEBUG: buffer now has {buffer.get_product_count()} products")

            # Phase 6: Extract tip and quick replies
            buffer.extract_and_set_tip()
            buffer.parse_quick_replies()

            # Phase 7: Yield completion thinking event
            completion_event = thinking_manager.get_completion_event()
            if completion_event:
                yield SSEEvent("thinking", completion_event.to_sse_data())

            # Phase 8: Yield final results
            snapshot = buffer.snapshot()
            logger.info(f"📊 DEBUG: snapshot.products has {len(snapshot.products)} products")
            logger.info(f"📊 DEBUG: snapshot.products truthy: {bool(snapshot.products)}")
            logger.info(f"📊 DEBUG: snapshot.text length: {len(snapshot.text)} chars")
            logger.info(f"📊 DEBUG: snapshot.text preview: {snapshot.text[:200] if snapshot.text else '[EMPTY]'}...")

            # Yield text
            yield SSEEvent("text", {"content": snapshot.text})


            # Yield products
            if snapshot.products:
                logger.info(f"📊 DEBUG: Yielding products SSE event with {len(snapshot.products)} products")
                # Format products as markdown for frontend injection
                formatted_products = self._format_products_markdown(snapshot.products)
                yield SSEEvent("products", {"content": formatted_products})

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
                "model_used": selected_model or self.config.model_name,
            })
            
            # Record success for circuit breaker
            if self.hybrid_manager and selected_model:
                self.hybrid_manager.record_success(selected_model)

        except EmptyResponseError as e:
            logger.error(f"Empty response in stream: {e}")
            
            # Attempt fallback retry (ONE attempt only, matching SAFETY pattern)
            if self.hybrid_manager and selected_model and not safety_retry_attempted:
                fallback_trigger = FallbackTrigger()
                decision = fallback_trigger.analyze_exception(e)
                
                if decision.should_fallback:
                    fallback_model = self.hybrid_manager.get_fallback_model(selected_model)
                    if fallback_model and fallback_model != selected_model:
                        logger.info(f"🔄 Fallback retry for empty response: {fallback_model}")
                        safety_retry_attempted = True  # Prevent infinite retries
                        
                        # Record failure before retry
                        self.hybrid_manager.record_failure(exception=e)
                        
                        # Re-create chat session with fallback model
                        chat = await self._create_chat_session(context, model_override=fallback_model)
                        
                        # Re-create loop with new session
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
                        
                        # Clear buffer for fresh start
                        buffer.clear()
                        
                        try:
                            # Re-execute streaming loop with fallback model
                            state = await loop.execute_streaming(enhanced_message)
                            logger.info(
                                f"✅ Empty response fallback complete: {len(state.accumulated_text)} chars"
                            )
                            
                            # Emit retry thinking event
                            retry_event = thinking_manager.get_retry_event(len(state.all_products))
                            yield SSEEvent("thinking", retry_event.to_sse_data())
                            
                            # Continue with normal flow - set buffer state
                            buffer.set_text(state.accumulated_text)
                            buffer.add_products(state.all_products)
                            buffer.extract_and_set_tip()
                            buffer.parse_quick_replies()
                            
                            # Proceed to snapshot and response
                            snapshot = buffer.get_snapshot()
                            if snapshot.text:
                                yield SSEEvent("text", {"content": snapshot.text})
                            if snapshot.products:
                                formatted_products = self._format_products_markdown(snapshot.products)
                                yield SSEEvent("products", {"content": formatted_products})
                            if snapshot.tip:
                                yield SSEEvent("tip", {"content": snapshot.tip})
                            if snapshot.quick_replies:
                                yield SSEEvent("quick_replies", {"replies": snapshot.quick_replies})
                            
                            # Save and done
                            save_attempted = True
                            await self._save_context(context, chat)
                            yield SSEEvent("done", {
                                "success": True,
                                "session_id": context.session_id,
                                "elapsed_seconds": context.elapsed_seconds(),
                                "model_used": fallback_model,
                                "fallback_used": True,
                            })
                            
                            self.hybrid_manager.record_success(fallback_model)
                            return  # Success - exit generator
                            
                        except Exception as retry_error:
                            logger.error(f"Fallback retry also failed: {retry_error}")
                            # Fall through to error response below
            
            # No fallback available or fallback failed - return error
            if self.hybrid_manager and selected_model:
                self.hybrid_manager.record_failure(exception=e)
            error = get_error_response("empty_response")
            yield SSEEvent("error", {
                "code": error.error_code,
                "message": error.message_georgian,
                "can_retry": error.can_retry,
            })

        except LoopTimeoutError as e:
            logger.error(f"Timeout in stream: {e}")
            # Record failure for circuit breaker
            if self.hybrid_manager and selected_model:
                self.hybrid_manager.record_failure(exception=e)
            error = get_error_response("timeout")
            yield SSEEvent("error", {
                "code": error.error_code,
                "message": error.message_georgian,
                "can_retry": error.can_retry,
            })

        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            # Record failure for circuit breaker
            if self.hybrid_manager and selected_model:
                self.hybrid_manager.record_failure(exception=e)
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

    async def _create_chat_session(
        self, 
        context: RequestContext,
        model_override: Optional[str] = None,
    ) -> Any:
        """
        Create Gemini chat session with loaded context.

        Args:
            context: RequestContext with history and profile
            model_override: Optional model name for fallback routing

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
            model_override=model_override,
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
    # MESSAGE ENHANCEMENT (Search-First Architecture)
    # =========================================================================

    def _is_product_query(
        self, message: str, history_len: int
    ) -> tuple[bool, Optional[str]]:
        """
        Determine if message warrants pre-emptive product search.

        Search-First Architecture: Detect product intent and extract search
        query BEFORE sending to Gemini, enabling product injection that
        eliminates the function-calling round-trip.

        Returns:
            tuple: (should_search, extracted_query)
                - should_search: True if we should pre-fetch products
                - extracted_query: The search keyword to use

        Confidence: ~92% accuracy on Georgian supplement queries
        """
        # RULE 0: Skip if mid-conversation (products likely already discussed)
        if history_len > 4:  # More than 2 exchanges
            logger.debug(
                f"🔍 Search-First: Skipping (history_len={history_len} > 4)"
            )
            return False, None

        msg = message.lower().strip()

        # =====================================================================
        # NEGATIVE FILTERS (Check these FIRST - high precision rejection)
        # =====================================================================
        for marker in NEGATIVE_MARKERS:
            if marker in msg:
                logger.debug(f"🚫 Search-First: Negative filter matched: '{marker}'")
                return False, None

        # =====================================================================
        # POSITIVE FILTERS (Product intent indicators)
        # =====================================================================

        # Check for product keyword
        found_keyword = None
        for keyword in PRODUCT_KEYWORDS:
            if keyword in msg:
                found_keyword = keyword
                break

        if not found_keyword:
            # No product keyword → definitely not a product query
            return False, None

        # Check for intent signal
        has_intent = any(verb in msg for verb in INTENT_VERBS)
        is_question = "?" in message or "რა " in msg or "რომელ" in msg

        if has_intent or is_question:
            # Strong product query signal
            logger.info(f"✅ Search-First: Product query detected: '{found_keyword}'")
            return True, found_keyword

        # Has product keyword but no clear intent - be conservative
        logger.debug(
            f"🤔 Search-First: Keyword '{found_keyword}' found but no intent signal"
        )
        return False, None

    def _format_products_for_injection(self, products: List[Dict[str, Any]]) -> str:
        """
        Format products for context injection.

        Creates a simple numbered list for Gemini to reference.
        Limit to 5 products max to avoid context bloat.

        Args:
            products: List of product dicts

        Returns:
            Formatted product list string
        """
        lines = []
        for i, p in enumerate(products[:5], 1):
            name = p.get("name", "N/A")
            price = p.get("price", "?")
            line = f"{i}. {name} - {price}₾"
            if p.get("brand"):
                line += f" ({p['brand']})"
            lines.append(line)
        return "\n".join(lines)

    def _enhance_message(self, context: RequestContext) -> str:
        """
        Enhance user message with pre-fetched product context.

        Search-First Architecture: Inject product catalog data
        BEFORE Gemini's first response, eliminating the function-calling
        round-trip that adds 4-5 seconds of latency.

        Steps:
        1. Detect if this is a product query using _is_product_query()
        2. If yes, pre-fetch products using vector_search_products()
        3. Format and inject into message with framing template
        4. Return enhanced message for Gemini

        Args:
            context: RequestContext with message and history

        Returns:
            Enhanced message string (or original if not product query)
        """
        from app.tools.user_tools import vector_search_products

        history_len = len(context.history) if context.history else 0

        # Check if this is a product query
        should_search, search_query = self._is_product_query(
            context.message, history_len
        )

        if not should_search:
            return context.message

        # Execute search with graceful fallback
        try:
            logger.info(f"🔍 Search-First: Pre-fetching for '{search_query}'")
            result = vector_search_products(query=search_query, limit=5)

            if result.get("count", 0) == 0:
                logger.info(
                    "🔍 Search-First: No products found, skipping injection"
                )
                return context.message

            # Format products for injection
            products_text = self._format_products_for_injection(result["products"])

            # Build enhanced message with framing template
            enhanced = INJECTION_TEMPLATE.format(
                count=result["count"],
                products=products_text,
                message=context.message,
            )

            logger.info(
                f"🔍 Search-First: Injected {result['count']} products into context"
            )
            return enhanced

        except Exception as e:
            logger.warning(
                f"🔍 Search-First error: {e}, falling back to original message"
            )
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

    def _format_products_markdown(self, products: List[Dict[str, Any]]) -> str:
        """
        Format products as markdown for frontend display.

        Creates product cards with name, brand, price, and URL.

        Args:
            products: List of product dicts

        Returns:
            Markdown-formatted string
        """
        if not products:
            return ""

        lines = []
        for p in products[:5]:  # Limit to first 5 products
            name = p.get("name", "პროდუქტი")
            brand = p.get("brand", "")
            price = p.get("price", 0)
            url = p.get("url", "")
            servings = p.get("servings")

            # Calculate price per serving if available
            price_per = f" · {price/servings:.2f} ₾/პორცია" if servings and price else ""

            lines.append(f"**{name}**")
            if brand:
                lines.append(f"*{brand}*")
            lines.append(f"**{price} ₾**{price_per}")
            if url:
                lines.append(f"[შეიძინე]({url})")
            lines.append("")  # Empty line between products

        return "\n".join(lines)


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
