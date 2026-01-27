"""
Scoop AI Core Types (v2.0)
==========================

Shared dataclasses, enums, and type definitions for the conversation engine.

This module defines the contract between components, ensuring type safety
and clear data flow throughout the engine.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# =============================================================================
# ENUMS
# =============================================================================

class ResponseMode(Enum):
    """
    Determines how the engine returns results.

    SYNC: Returns complete ConversationResult (for /chat endpoint)
    STREAM: Yields SSE events as AsyncIterator (for /chat/stream endpoint)
    """
    SYNC = "sync"
    STREAM = "stream"


class RoundResult(Enum):
    """
    Outcome of a single function calling round.

    Used by FunctionCallingLoop to determine next action.
    """
    COMPLETE = "complete"     # Got text response, loop should exit
    CONTINUE = "continue"     # Got function calls, need another round
    EMPTY = "empty"           # No text, no function calls (problem state)
    ERROR = "error"           # Exception occurred during round


class ThinkingStrategy(Enum):
    """
    v2.0 Thinking UI strategies (simplified from v1.0).

    DEPRECATED in v1.0:
    - SIMULATED: Hardcoded Georgian steps (removed)
    - TRANSLATED: Real thoughts + Gemini translation (removed)

    SUPPORTED in v2.0:
    - NONE: No thinking UI (fastest, most reliable)
    - SIMPLE_LOADER: Static loading message
    - NATIVE: Use SDK's thinking tokens (when SDK bug #4090 is fixed)
    """
    NONE = "none"
    SIMPLE_LOADER = "simple_loader"
    NATIVE = "native"


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class EngineConfig:
    """
    Configuration for ConversationEngine.

    These settings control the behavior of the unified engine
    and can be overridden via environment variables.
    """
    # Function calling settings
    max_function_rounds: int = 3
    max_unique_search_queries: int = 3

    # Timeout settings
    gemini_timeout_seconds: int = 30

    # Thinking UI settings
    thinking_strategy: ThinkingStrategy = ThinkingStrategy.NONE

    # Retry settings
    retry_on_empty: bool = True  # Retry once if texts=0 with products

    # Output settings
    max_output_tokens: int = 8192
    temperature: float = 0.7


# =============================================================================
# REQUEST CONTEXT
# =============================================================================

@dataclass
class RequestContext:
    """
    Encapsulates all context for a single request.

    This replaces the scattered variables in v1.0's chat_stream function.
    Passed through the pipeline to maintain clean state management.
    """
    # Request identifiers
    user_id: str
    message: str
    session_id: Optional[str] = None
    mode: ResponseMode = ResponseMode.SYNC

    # Loaded context (populated during processing)
    history: List[Dict[str, Any]] = field(default_factory=list)
    user_profile: Optional[Dict[str, Any]] = None

    # Timing metadata
    started_at: datetime = field(default_factory=datetime.utcnow)

    def elapsed_seconds(self) -> float:
        """Calculate elapsed time since request started."""
        return (datetime.utcnow() - self.started_at).total_seconds()


# =============================================================================
# FUNCTION CALLING TYPES
# =============================================================================

@dataclass
class FunctionCall:
    """
    Normalized representation of a function call from Gemini.

    Abstracts away SDK-specific structures for cleaner processing.
    """
    name: str
    args: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sdk_part(cls, part: Any) -> Optional["FunctionCall"]:
        """
        Create FunctionCall from SDK's function_call part.

        Args:
            part: A part object from Gemini response

        Returns:
            FunctionCall if part contains function_call, None otherwise
        """
        if hasattr(part, 'function_call') and part.function_call:
            fc = part.function_call
            return cls(
                name=fc.name,
                args=dict(fc.args) if fc.args else {}
            )
        return None


@dataclass
class RoundOutput:
    """
    Output from a single round of function calling.

    Contains all information needed to decide next action
    and accumulate results across rounds.
    """
    result: RoundResult
    text: str = ""
    function_calls: List[FunctionCall] = field(default_factory=list)
    function_responses: List[Dict[str, Any]] = field(default_factory=list)
    products_found: List[Dict[str, Any]] = field(default_factory=list)
    thoughts: List[str] = field(default_factory=list)  # For logging only, NOT for fallback
    error: Optional[str] = None
    finish_reason: Optional[str] = None  # Track Gemini finish reason (STOP, SAFETY, etc.)

    @property
    def has_text(self) -> bool:
        """Check if round produced meaningful text."""
        return bool(self.text.strip())

    @property
    def has_function_calls(self) -> bool:
        """Check if round produced function calls."""
        return bool(self.function_calls)


@dataclass
class LoopState:
    """
    Mutable state tracked across function calling rounds.

    Encapsulates all the scattered variables from v1.0's manual loop.
    """
    # Accumulated results
    accumulated_text: str = ""
    all_products: List[Dict[str, Any]] = field(default_factory=list)
    all_thoughts: List[str] = field(default_factory=list)  # For debugging only

    # Deduplication tracking
    executed_queries: set = field(default_factory=set)
    product_ids: set = field(default_factory=set)

    # Round tracking
    rounds_completed: int = 0
    retry_attempted: bool = False

    # TIP tracking
    native_tip_extracted: bool = False
    
    # SAFETY fallback tracking
    last_finish_reason: Optional[str] = None  # Last round's finish reason (for SAFETY detection)

    def add_products(self, products: List[Dict[str, Any]]) -> int:
        """
        Add products with deduplication.

        Args:
            products: List of product dicts to add

        Returns:
            Number of new products actually added
        """
        added = 0
        for product in products:
            pid = product.get("id") or product.get("_id") or product.get("product_id")
            if pid:
                pid_str = str(pid)
                if pid_str not in self.product_ids:
                    self.product_ids.add(pid_str)
                    self.all_products.append(product)
                    added += 1
            else:
                # No ID, add anyway (can't dedupe)
                self.all_products.append(product)
                added += 1
        return added

    def query_already_executed(self, query: str) -> bool:
        """Check if a search query was already executed."""
        return query.lower().strip() in self.executed_queries

    def mark_query_executed(self, query: str) -> None:
        """Mark a search query as executed."""
        self.executed_queries.add(query.lower().strip())

    def can_execute_more_queries(self, max_queries: int) -> bool:
        """Check if we can execute more unique queries."""
        return len(self.executed_queries) < max_queries


# =============================================================================
# RESPONSE TYPES
# =============================================================================

@dataclass
class ConversationResult:
    """
    Unified result type for conversation responses.

    Used by both sync and stream modes as the final output structure.
    """
    # Core response content
    text: str
    products: List[Dict[str, Any]]
    tip: Optional[str]
    quick_replies: List[Dict[str, str]]

    # Status
    success: bool
    error: Optional[str] = None
    error_code: Optional[str] = None

    # Metadata (timing, token usage, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "response_text_geo": self.text,
            "products": self.products,
            "tip": self.tip,
            "quick_replies": self.quick_replies,
            "success": self.success,
            "error": self.error,
            "metadata": self.metadata,
        }


@dataclass
class ErrorResponse:
    """
    Structured error for frontend handling.

    Provides Georgian messages and recovery suggestions.
    """
    error_code: str
    message_georgian: str
    can_retry: bool
    suggestion: Optional[str] = None

    def to_conversation_result(self) -> ConversationResult:
        """Convert to ConversationResult for consistent API response."""
        return ConversationResult(
            text=self.message_georgian,
            products=[],
            tip=None,
            quick_replies=[],
            success=False,
            error=self.error_code,
            error_code=self.error_code,
            metadata={"suggestion": self.suggestion} if self.suggestion else {},
        )


# =============================================================================
# PREDEFINED ERROR RESPONSES
# =============================================================================

ERROR_RESPONSES = {
    "empty_response": ErrorResponse(
        error_code="empty_response",
        message_georgian="პასუხის გენერირება ვერ მოხერხდა. გთხოვთ სცადოთ სხვანაირად.",
        can_retry=True,
        suggestion="სცადეთ უფრო კონკრეტული კითხვა"
    ),
    "timeout": ErrorResponse(
        error_code="timeout",
        message_georgian="მოთხოვნას ძალიან დიდი დრო დასჭირდა.",
        can_retry=True,
        suggestion="სცადეთ უფრო მარტივი კითხვა"
    ),
    "no_products": ErrorResponse(
        error_code="no_products",
        message_georgian="პროდუქტები ვერ მოიძებნა თქვენი კრიტერიუმებით.",
        can_retry=True,
        suggestion="სცადეთ სხვა საძიებო სიტყვები"
    ),
    "internal_error": ErrorResponse(
        error_code="internal_error",
        message_georgian="დროებითი შეცდომა. გთხოვთ სცადოთ ხელახლა.",
        can_retry=True,
        suggestion=None
    ),
    "content_blocked": ErrorResponse(
        error_code="content_blocked",
        message_georgian="ბოდიში, ეს კითხვა ვერ დამუშავდა. სცადეთ სხვანაირად.",
        can_retry=True,
        suggestion=None
    ),
}


def get_error_response(error_code: str) -> ErrorResponse:
    """
    Get predefined error response by code.

    Falls back to internal_error if code not found.
    """
    return ERROR_RESPONSES.get(error_code, ERROR_RESPONSES["internal_error"])
