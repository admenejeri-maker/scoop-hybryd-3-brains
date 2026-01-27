"""
Scoop AI Gemini Adapter (v2.0)
==============================

Wraps Google Gemini SDK calls, providing a clean interface for the ConversationEngine.

Key Responsibilities:
1. Create chat sessions with proper configuration
2. ENFORCE manual function calling (AFC disabled)
3. Handle retries and timeouts
4. Provide consistent error handling

Design Principle: Isolate SDK-specific code so the engine is testable.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.types import (
    GenerateContentConfig,
    SafetySetting,
    HarmCategory,
    HarmBlockThreshold,
    Part,
    UserContent,
    ModelContent,
)

logger = logging.getLogger(__name__)


# =============================================================================
# SAFETY SETTINGS (Reused from main.py)
# =============================================================================

DEFAULT_SAFETY_SETTINGS = [
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
    SafetySetting(
        category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
]


# =============================================================================
# RETRY CONFIGURATION
# =============================================================================

RETRY_EXCEPTIONS = (
    "ResourceExhausted",  # 429 - Rate limit
    "ServiceUnavailable",  # 503 - Temporary outage
    "DeadlineExceeded",   # Timeout
)


@dataclass
class GeminiConfig:
    """Configuration for GeminiAdapter."""
    model_name: str = "gemini-2.5-pro"  # Stable GA model
    temperature: float = 1.0  # Gemini 3 recommended default
    top_p: float = 0.95
    top_k: int = 40
    max_output_tokens: int = 8192
    timeout_seconds: int = 30
    enable_safety_settings: bool = True
    max_retries: int = 3
    base_retry_delay: float = 2.0


class GeminiAdapter:
    """
    Adapter for Google Gemini SDK.

    CRITICAL DESIGN DECISION:
    - automatic_function_calling is ALWAYS disabled
    - This ensures both /chat and /chat/stream use the same manual FC logic
    - The FunctionCallingLoop handles all function execution

    USAGE:
        adapter = GeminiAdapter(api_key="...", config=GeminiConfig())

        chat = await adapter.create_chat(
            history=sdk_history,
            tools=GEMINI_TOOLS,
            system_instruction="You are Scoop AI..."
        )

        # Use chat session with FunctionCallingLoop
        response = await chat.send_message("Hello")
    """

    def __init__(
        self,
        api_key: str,
        config: Optional[GeminiConfig] = None,
    ):
        """
        Initialize GeminiAdapter.

        Args:
            api_key: Gemini API key
            config: Optional configuration
        """
        if not api_key:
            raise ValueError("Gemini API key is required")

        self.client = genai.Client(api_key=api_key)
        self.config = config or GeminiConfig()

        logger.info(
            f"GeminiAdapter initialized: model={self.config.model_name}, "
            f"timeout={self.config.timeout_seconds}s"
        )

    # =========================================================================
    # CHAT SESSION CREATION
    # =========================================================================

    def create_chat(
        self,
        history: Optional[List[Any]] = None,
        tools: Optional[List[Any]] = None,
        system_instruction: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Any:
        """
        Create a new chat session with Manual Function Calling.

        CRITICAL: automatic_function_calling is ALWAYS disabled.
        This is the key architectural decision for v2.0 unified behavior.

        Args:
            history: Optional conversation history (SDK format)
            tools: Optional list of tool definitions
            system_instruction: Optional system prompt
            model_override: Optional model name to use instead of config default

        Returns:
            AsyncChat session ready for send_message calls
        """
        # Determine which model to use
        model_name = model_override or self.config.model_name
        
        # Build configuration with AFC DISABLED
        chat_config = GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            safety_settings=DEFAULT_SAFETY_SETTINGS if self.config.enable_safety_settings else None,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            top_k=self.config.top_k,
            max_output_tokens=self.config.max_output_tokens,
            # CRITICAL: Always disable AFC for manual function calling
            # This is the v2.0 architectural decision - unified behavior
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True  # NEVER enable this
            ),
            # NOTE: ThinkingConfig intentionally omitted to avoid SDK bug #4090
            # (Gemini 3 + streaming + tools + ThinkingConfig = empty text)
        )

        # Create async chat session
        # NOTE: gemini_client.aio.chats.create() is SYNC - returns AsyncChat directly
        chat = self.client.aio.chats.create(
            model=model_name,
            history=history if history else None,
            config=chat_config,
        )

        logger.info(
            f"Created chat session: model={self.config.model_name}, "
            f"history_len={len(history) if history else 0}, "
            f"tools={len(tools) if tools else 0}"
        )

        return chat

    # =========================================================================
    # HISTORY CONVERSION
    # =========================================================================

    def bson_to_sdk_history(self, bson_history: List[Dict[str, Any]]) -> List[Any]:
        """
        Convert BSON history (from MongoDB) to SDK format.

        Args:
            bson_history: List of history entries from MongoDB

        Returns:
            List of UserContent/ModelContent objects
        """
        sdk_history = []

        for entry in bson_history:
            role = entry.get("role", "user")
            parts = []

            for part in entry.get("parts", []):
                # 1. Text Parts
                if "text" in part and part["text"]:
                    parts.append(Part.from_text(text=part["text"]))
                
                # 2. Function Calls (Model role)
                elif "function_call" in part and part["function_call"]:
                    fc = part["function_call"]
                    parts.append(
                        Part.from_function_call(
                            name=fc["name"],
                            args=fc.get("args", {})
                        )
                    )
                
                # 3. Function Responses (User role)
                elif "function_response" in part and part["function_response"]:
                    fr = part["function_response"]
                    parts.append(
                        Part.from_function_response(
                            name=fr["name"],
                            response=fr["response"]
                        )
                    )

            if parts:
                if role == "user":
                    sdk_history.append(UserContent(parts=parts))
                else:
                    sdk_history.append(ModelContent(parts=parts))

        return sdk_history

    def sdk_history_to_bson(self, sdk_history: Any) -> List[Dict[str, Any]]:
        """
        Convert SDK history to BSON format for MongoDB storage.

        Args:
            sdk_history: SDK history (from chat.get_history())

        Returns:
            List of BSON-serializable dicts
        """
        bson_history = []

        try:
            history_list = list(sdk_history) if sdk_history else []
        except Exception:
            return []

        for content in history_list:
            # Determine role
            role = "model"
            if isinstance(content, UserContent):
                role = "user"
            elif hasattr(content, 'role'):
                role = content.role

            entry = {"role": role, "parts": []}

            if hasattr(content, 'parts') and content.parts:
                for part in content.parts:
                    # Text parts
                    if hasattr(part, 'text') and part.text:
                        # Skip thought parts
                        if hasattr(part, 'thought') and part.thought:
                            continue
                        entry["parts"].append({"text": part.text})

                    # Function call parts
                    elif hasattr(part, 'function_call') and part.function_call:
                        fc = part.function_call
                        entry["parts"].append({
                            "function_call": {
                                "name": fc.name,
                                "args": dict(fc.args) if fc.args else {}
                            }
                        })

                    # Function response parts
                    elif hasattr(part, 'function_response') and part.function_response:
                        fr = part.function_response
                        entry["parts"].append({
                            "function_response": {
                                "name": fr.name,
                                "response": fr.response
                            }
                        })

            if entry["parts"]:
                bson_history.append(entry)

        return bson_history

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def call_with_retry(
        self,
        func,
        *args,
        **kwargs
    ) -> Any:
        """
        Call a function with exponential backoff retry.

        Handles common Gemini errors:
        - 429 (Rate limit)
        - 503 (Service unavailable)
        - Timeout

        Args:
            func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            Last exception if all retries fail
        """
        last_exception = None

        for attempt in range(self.config.max_retries):
            try:
                # Apply timeout
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=self.config.timeout_seconds
                )
                return result

            except asyncio.TimeoutError:
                last_exception = asyncio.TimeoutError(
                    f"Timeout after {self.config.timeout_seconds}s"
                )
                logger.warning(f"Timeout on attempt {attempt + 1}")

            except Exception as e:
                last_exception = e
                error_type = type(e).__name__

                # Check if retryable
                if any(exc in error_type for exc in RETRY_EXCEPTIONS):
                    delay = self.config.base_retry_delay * (2 ** attempt)
                    logger.warning(
                        f"Retryable error {error_type} on attempt {attempt + 1}, "
                        f"retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    # Non-retryable error
                    raise

        # All retries exhausted
        raise last_exception

    def build_function_response_parts(
        self,
        responses: List[Dict[str, Any]]
    ) -> List[Part]:
        """
        Build function response parts for sending back to Gemini.

        Args:
            responses: List of {name, response} dicts

        Returns:
            List of Part objects with function responses
        """
        parts = []
        for resp in responses:
            parts.append(
                Part.from_function_response(
                    name=resp["name"],
                    response=resp["response"]
                )
            )
        return parts

    # =========================================================================
    # STREAMING SUPPORT
    # =========================================================================

    def create_streaming_chat(
        self,
        history: Optional[List[Any]] = None,
        tools: Optional[List[Any]] = None,
        system_instruction: Optional[str] = None,
    ) -> Any:
        """
        Create a chat session optimized for streaming.

        Same as create_chat but explicitly for streaming context.
        AFC is ALWAYS disabled.

        Args:
            history: Optional conversation history
            tools: Optional tool definitions
            system_instruction: Optional system prompt

        Returns:
            AsyncChat session for streaming
        """
        # Use the same create_chat - AFC disabled ensures streaming compatibility
        return self.create_chat(
            history=history,
            tools=tools,
            system_instruction=system_instruction,
        )

    async def stream_send_message(
        self,
        chat: Any,
        message: Any,
    ):
        """
        Send a message and get streaming response.

        This is a helper that wraps send_message_stream on the chat session.

        Args:
            chat: AsyncChat session
            message: Message to send (string or function responses)

        Yields:
            Response chunks from Gemini
        """
        try:
            async for chunk in await chat.send_message_stream(message):
                yield chunk
        except Exception as e:
            logger.error(f"Stream error: {e}")
            raise

    async def generate_content_stream(
        self,
        contents: Any,
        tools: Optional[List[Any]] = None,
        system_instruction: Optional[str] = None,
    ):
        """
        Generate content with streaming using direct API call.

        Used when not using chat session (single-turn).

        Args:
            contents: Content to send
            tools: Optional tool definitions
            system_instruction: Optional system prompt

        Yields:
            Response chunks
        """
        config = GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            safety_settings=DEFAULT_SAFETY_SETTINGS if self.config.enable_safety_settings else None,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            top_k=self.config.top_k,
            max_output_tokens=self.config.max_output_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        try:
            async for chunk in self.client.aio.models.generate_content_stream(
                model=self.config.model_name,
                contents=contents,
                config=config,
            ):
                yield chunk
        except Exception as e:
            logger.error(f"Generate content stream error: {e}")
            raise

    # =========================================================================
    # RESPONSE PARSING HELPERS
    # =========================================================================

    def extract_parts_from_chunk(self, chunk: Any) -> Dict[str, Any]:
        """
        Extract parts from a streaming chunk.

        Categorizes parts into text, function_calls, and thoughts.

        Args:
            chunk: A streaming response chunk

        Returns:
            Dict with 'text', 'function_calls', 'thoughts' lists
        """
        result = {
            "text": [],
            "function_calls": [],
            "thoughts": [],
            "has_content": False,
        }

        if not hasattr(chunk, 'candidates') or not chunk.candidates:
            # Try direct text access
            if hasattr(chunk, 'text') and chunk.text:
                result["text"].append(chunk.text)
                result["has_content"] = True
            return result

        candidate = chunk.candidates[0]
        if not hasattr(candidate, 'content') or not candidate.content:
            return result

        # DEFENSIVE: Check parts is not None before iterating
        parts = candidate.content.parts
        if parts is None:
            return result

        for part in parts:
            # Check for thought
            if hasattr(part, 'thought') and part.thought:
                thought_text = getattr(part, 'text', '') or ''
                if thought_text:
                    result["thoughts"].append(thought_text)
                    result["has_content"] = True
                continue

            # Check for function call
            if hasattr(part, 'function_call') and part.function_call:
                fc = part.function_call
                result["function_calls"].append({
                    "name": fc.name,
                    "args": dict(fc.args) if fc.args else {},
                })
                result["has_content"] = True
                continue

            # Check for text
            if hasattr(part, 'text') and part.text:
                result["text"].append(part.text)
                result["has_content"] = True

        return result

    def is_function_call_chunk(self, chunk: Any) -> bool:
        """
        Check if a chunk contains function calls.

        Args:
            chunk: Response chunk

        Returns:
            True if chunk has function calls
        """
        parts = self.extract_parts_from_chunk(chunk)
        return len(parts["function_calls"]) > 0

    def has_text_content(self, chunk: Any) -> bool:
        """
        Check if a chunk contains text content.

        Args:
            chunk: Response chunk

        Returns:
            True if chunk has text
        """
        parts = self.extract_parts_from_chunk(chunk)
        return len(parts["text"]) > 0

    # =========================================================================
    # EMBEDDING (for vector search)
    # =========================================================================

    async def embed_content(
        self,
        text: str,
        model: str = "text-embedding-004"
    ) -> List[float]:
        """
        Generate embedding for text content.

        Used for semantic product search.

        Args:
            text: Text to embed
            model: Embedding model name

        Returns:
            List of embedding values
        """
        try:
            result = await asyncio.to_thread(
                genai.embed_content,
                model=model,
                content=text
            )
            return result.get("embedding", [])
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return []


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_gemini_adapter(
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> GeminiAdapter:
    """
    Factory function to create GeminiAdapter with settings.

    Args:
        api_key: Optional API key (falls back to settings)
        model_name: Optional model name (falls back to settings)
        timeout_seconds: Optional timeout (falls back to settings)

    Returns:
        Configured GeminiAdapter
    """
    from config import settings

    config = GeminiConfig(
        model_name=model_name or settings.model_name,
        timeout_seconds=timeout_seconds or settings.gemini_timeout_seconds,
        max_output_tokens=settings.max_output_tokens,
        enable_safety_settings=settings.enable_safety_settings,
    )

    return GeminiAdapter(
        api_key=api_key or settings.gemini_api_key,
        config=config,
    )
