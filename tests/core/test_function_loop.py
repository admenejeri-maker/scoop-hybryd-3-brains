"""
Tests for FunctionCallingLoop (v2.0)
====================================

Comprehensive tests for the multi-round function calling logic.

Tests cover:
1. Basic loop execution (happy path)
2. Function call processing
3. Retry logic (texts=0 with products)
4. EmptyResponseError handling
5. Deduplication
6. Max rounds behavior
7. Timeout handling
"""

import sys
import os

# Add backend root to path for app imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.function_loop import (
    FunctionCallingLoop,
    LoopConfig,
    EmptyResponseError,
    LoopTimeoutError,
)
from app.core.tool_executor import ToolExecutor, ToolResult
from app.core.types import (
    FunctionCall,
    RoundResult,
    RoundOutput,
    LoopState,
)


# =============================================================================
# MOCK HELPERS
# =============================================================================

@dataclass
class MockPart:
    """Mock Gemini response part."""
    text: Optional[str] = None
    thought: bool = False
    function_call: Optional[Any] = None


@dataclass
class MockFunctionCall:
    """Mock function call object."""
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MockContent:
    """Mock content with parts."""
    parts: List[MockPart] = field(default_factory=list)


@dataclass
class MockCandidate:
    """Mock response candidate."""
    content: Optional[MockContent] = None


@dataclass
class MockResponse:
    """Mock Gemini response."""
    candidates: List[MockCandidate] = field(default_factory=list)
    text: Optional[str] = None


class MockChatSession:
    """
    Mock chat session for testing.

    Allows configuration of responses for each call.
    """

    def __init__(self, responses: List[MockResponse] = None):
        self.responses = responses or []
        self.call_count = 0
        self.messages_received = []

    async def send_message(self, message: Any) -> MockResponse:
        """Return next configured response."""
        self.messages_received.append(message)

        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response

        # Default empty response
        return MockResponse(candidates=[])

    async def send_message_stream(self, message: Any):
        """Return responses as async iterator."""
        self.messages_received.append(message)

        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            yield response


def create_text_response(text: str) -> MockResponse:
    """Create a response with text content."""
    return MockResponse(
        candidates=[
            MockCandidate(
                content=MockContent(
                    parts=[MockPart(text=text)]
                )
            )
        ]
    )


def create_fc_response(name: str, args: Dict[str, Any] = None) -> MockResponse:
    """Create a response with a function call."""
    return MockResponse(
        candidates=[
            MockCandidate(
                content=MockContent(
                    parts=[
                        MockPart(
                            function_call=MockFunctionCall(name=name, args=args or {})
                        )
                    ]
                )
            )
        ]
    )


def create_empty_response() -> MockResponse:
    """Create an empty response (no text, no FC)."""
    return MockResponse(candidates=[MockCandidate(content=MockContent(parts=[]))])


def create_thought_response(thought_text: str) -> MockResponse:
    """Create a response with thought part only."""
    return MockResponse(
        candidates=[
            MockCandidate(
                content=MockContent(
                    parts=[MockPart(text=thought_text, thought=True)]
                )
            )
        ]
    )


class MockToolExecutor(ToolExecutor):
    """
    Mock tool executor for testing.

    Returns configurable results for function calls.
    """

    def __init__(
        self,
        search_results: Dict[str, Any] = None,
        profile: Dict[str, Any] = None,
    ):
        # Don't call super().__init__ to avoid user_id requirement
        self.user_id = "test_user"
        self.user_profile = profile or {}
        self._search_results = search_results or {"products": [], "count": 0}
        self._executed_queries = set()
        self._max_unique_queries = 3
        self._all_products = []
        self.execute_calls = []

    async def execute(self, call: FunctionCall) -> ToolResult:
        """Track call and return configured result."""
        self.execute_calls.append(call)

        if call.name == "search_products":
            products = self._search_results.get("products", [])
            self._all_products.extend(products)
            return ToolResult(
                name=call.name,
                response=self._search_results,
                products=products,
            )

        if call.name == "get_user_profile":
            return ToolResult(
                name=call.name,
                response=self.user_profile,
            )

        return ToolResult(name=call.name, response={})

    async def execute_batch(
        self,
        calls: List[FunctionCall],
        dedupe_search: bool = True,
    ) -> List[ToolResult]:
        """Execute batch of calls."""
        results = []
        for call in calls:
            result = await self.execute(call)
            results.append(result)
        return results


# =============================================================================
# BASIC LOOP EXECUTION
# =============================================================================

class TestBasicLoopExecution:
    """Tests for basic loop execution flow."""

    @pytest.mark.asyncio
    async def test_single_round_with_text(self):
        """Test loop completes in single round with text response."""
        session = MockChatSession(responses=[
            create_text_response("გამარჯობა! აი რეკომენდაცია.")
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        state = await loop.execute("რა პროტეინი მირჩევთ?")

        assert state.accumulated_text == "გამარჯობა! აი რეკომენდაცია."
        assert state.rounds_completed == 1

    @pytest.mark.asyncio
    async def test_two_rounds_fc_then_text(self):
        """Test loop with function call round followed by text round."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "protein"}),
            create_text_response("მოვიძიე 5 პროდუქტი."),
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1", "name": "Whey"}], "count": 1}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        state = await loop.execute("მინდა პროტეინი")

        assert state.rounds_completed == 2
        assert state.accumulated_text == "მოვიძიე 5 პროდუქტი."
        assert len(executor.execute_calls) == 1
        assert executor.execute_calls[0].name == "search_products"

    @pytest.mark.asyncio
    async def test_multiple_function_calls_in_round(self):
        """Test handling multiple function calls in single round."""
        # Response with two function calls
        response = MockResponse(
            candidates=[
                MockCandidate(
                    content=MockContent(
                        parts=[
                            MockPart(function_call=MockFunctionCall("search_products", {"query": "protein"})),
                            MockPart(function_call=MockFunctionCall("get_user_profile", {})),
                        ]
                    )
                )
            ]
        )

        session = MockChatSession(responses=[
            response,
            create_text_response("Result text"),
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        state = await loop.execute("Test query")

        # Both function calls should be processed
        assert len(executor.execute_calls) == 2


# =============================================================================
# RETRY LOGIC
# =============================================================================

class TestRetryLogic:
    """Tests for retry on empty response."""

    @pytest.mark.asyncio
    async def test_retry_on_empty_with_products(self):
        """Test retry is triggered when texts=0 but products exist."""
        session = MockChatSession(responses=[
            # Round 1: Function call
            create_fc_response("search_products", {"query": "protein"}),
            # Round 2: Empty response (triggers retry)
            create_empty_response(),
            # Round 3 (retry): Text response
            create_text_response("აი რეკომენდაცია ნაპოვნი პროდუქტებზე."),
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1", "name": "Protein"}], "count": 1}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        state = await loop.execute("მინდა პროტეინი")

        assert state.retry_attempted is True
        assert state.accumulated_text == "აი რეკომენდაცია ნაპოვნი პროდუქტებზე."
        # Should have received summary prompt
        assert any("რეკომენდაცია" in str(m) for m in session.messages_received)

    @pytest.mark.asyncio
    async def test_no_retry_without_products(self):
        """Test no retry when empty response and no products."""
        session = MockChatSession(responses=[
            create_empty_response(),
        ])
        executor = MockToolExecutor()  # No products

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        with pytest.raises(EmptyResponseError) as exc_info:
            await loop.execute("კითხვა")

        assert exc_info.value.retry_attempted is False
        assert exc_info.value.products_found == 0

    @pytest.mark.asyncio
    async def test_only_one_retry(self):
        """Test retry happens only once."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "test"}),
            create_empty_response(),  # Trigger retry
            create_empty_response(),  # Still empty after retry
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1"}], "count": 1}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        with pytest.raises(EmptyResponseError) as exc_info:
            await loop.execute("test")

        assert exc_info.value.retry_attempted is True

    @pytest.mark.asyncio
    async def test_retry_disabled_in_config(self):
        """Test retry can be disabled via config."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "test"}),
            create_empty_response(),
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1"}], "count": 1}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            config=LoopConfig(enable_retry=False),
        )

        with pytest.raises(EmptyResponseError) as exc_info:
            await loop.execute("test")

        # Should fail immediately without retry
        assert exc_info.value.retry_attempted is False


# =============================================================================
# EMPTY RESPONSE ERROR
# =============================================================================

class TestEmptyResponseError:
    """Tests for EmptyResponseError handling."""

    @pytest.mark.asyncio
    async def test_error_contains_context(self):
        """Test EmptyResponseError contains useful context."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "test"}),
            create_empty_response(),
            create_empty_response(),  # After retry
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1"}, {"id": "2"}], "count": 2}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        with pytest.raises(EmptyResponseError) as exc_info:
            await loop.execute("test")

        error = exc_info.value
        assert error.products_found == 2
        assert error.retry_attempted is True
        assert error.rounds_completed >= 2

    @pytest.mark.asyncio
    async def test_error_on_first_round_empty(self):
        """Test error when first round is empty with no products."""
        session = MockChatSession(responses=[
            create_empty_response(),
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        with pytest.raises(EmptyResponseError):
            await loop.execute("test")


# =============================================================================
# MAX ROUNDS
# =============================================================================

class TestMaxRounds:
    """Tests for max rounds behavior."""

    @pytest.mark.asyncio
    async def test_max_rounds_reached_with_text(self):
        """Test loop exits cleanly when max rounds reached with text."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "a"}),
            create_fc_response("search_products", {"query": "b"}),
            create_text_response("Final text after many rounds"),
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1"}], "count": 1}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            config=LoopConfig(max_rounds=3),
        )

        state = await loop.execute("test")

        assert state.rounds_completed == 3
        assert "Final text" in state.accumulated_text

    @pytest.mark.asyncio
    async def test_max_rounds_exceeded_no_text(self):
        """Test EmptyResponseError when max rounds reached without text."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "a"}),
            create_fc_response("search_products", {"query": "b"}),
            create_fc_response("search_products", {"query": "c"}),
            create_empty_response(),  # Retry attempt
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1"}], "count": 1}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            config=LoopConfig(max_rounds=3),
        )

        with pytest.raises(EmptyResponseError):
            await loop.execute("test")

    @pytest.mark.asyncio
    async def test_custom_max_rounds(self):
        """Test custom max_rounds config."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "test"}),
            create_text_response("Done"),
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            config=LoopConfig(max_rounds=5),
        )

        state = await loop.execute("test")

        assert state.rounds_completed <= 5


# =============================================================================
# DEDUPLICATION
# =============================================================================

class TestDeduplication:
    """Tests for search query deduplication."""

    @pytest.mark.asyncio
    async def test_duplicate_fc_in_batch_filtered(self):
        """Test duplicate search_products in same batch is filtered."""
        # Response with two search_products calls
        response = MockResponse(
            candidates=[
                MockCandidate(
                    content=MockContent(
                        parts=[
                            MockPart(function_call=MockFunctionCall("search_products", {"query": "protein"})),
                            MockPart(function_call=MockFunctionCall("search_products", {"query": "whey"})),
                        ]
                    )
                )
            ]
        )

        session = MockChatSession(responses=[
            response,
            create_text_response("Done"),
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        await loop.execute("test")

        # Only first search should be executed
        search_calls = [c for c in executor.execute_calls if c.name == "search_products"]
        assert len(search_calls) == 1


# =============================================================================
# THOUGHT HANDLING
# =============================================================================

class TestThoughtHandling:
    """Tests for thought part handling."""

    @pytest.mark.asyncio
    async def test_thoughts_collected_but_not_used_as_fallback(self):
        """Test thoughts are collected for logging but not used as text fallback."""
        session = MockChatSession(responses=[
            create_thought_response("Internal reasoning..."),
            # Empty text response
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        with pytest.raises(EmptyResponseError):
            await loop.execute("test")

        # Thoughts should be in state but not used as fallback
        # (This is the key v2.0 change - no Option D)

    @pytest.mark.asyncio
    async def test_thought_callback_invoked(self):
        """Test thought callback is invoked when thought received."""
        thoughts_received = []

        async def on_thought(text: str):
            thoughts_received.append(text)

        # Response with thought followed by text
        response = MockResponse(
            candidates=[
                MockCandidate(
                    content=MockContent(
                        parts=[
                            MockPart(text="Thinking about protein...", thought=True),
                            MockPart(text="Here is the answer."),
                        ]
                    )
                )
            ]
        )

        session = MockChatSession(responses=[response])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            on_thought=on_thought,
        )

        await loop.execute("test")

        assert len(thoughts_received) == 1
        assert "Thinking" in thoughts_received[0]


# =============================================================================
# TIMEOUT
# =============================================================================

class TestTimeout:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout_raises_error(self):
        """Test timeout raises LoopTimeoutError."""
        async def slow_send_message(message):
            await asyncio.sleep(10)  # Longer than timeout
            return create_text_response("Too late")

        session = MockChatSession()
        session.send_message = slow_send_message
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            config=LoopConfig(timeout_seconds=0.1),
        )

        with pytest.raises(LoopTimeoutError):
            await loop.execute("test")


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

class TestStateManagement:
    """Tests for loop state management."""

    @pytest.mark.asyncio
    async def test_state_accumulates_across_rounds(self):
        """Test state accumulates text and products across rounds."""
        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "a"}),
            create_fc_response("search_products", {"query": "b"}),
            create_text_response("Final"),
        ])
        executor = MockToolExecutor(
            search_results={"products": [{"id": "1"}], "count": 1}
        )

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        state = await loop.execute("test")

        assert state.rounds_completed == 3
        # Products should accumulate (2 search calls)
        assert len(state.all_products) >= 1

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        """Test reset clears loop state."""
        session = MockChatSession(responses=[
            create_text_response("First run"),
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
        )

        await loop.execute("first")
        loop.reset()

        assert loop.state.rounds_completed == 0
        assert loop.state.accumulated_text == ""


# =============================================================================
# CALLBACKS
# =============================================================================

class TestCallbacks:
    """Tests for streaming callbacks."""

    @pytest.mark.asyncio
    async def test_text_callback_invoked(self):
        """Test text callback is invoked when text received."""
        text_chunks = []

        async def on_text_chunk(text: str):
            text_chunks.append(text)

        session = MockChatSession(responses=[
            create_text_response("Hello world"),
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            on_text_chunk=on_text_chunk,
        )

        await loop.execute("test")

        assert len(text_chunks) == 1
        assert "Hello" in text_chunks[0]

    @pytest.mark.asyncio
    async def test_function_call_callback_invoked(self):
        """Test function call callback is invoked."""
        fc_received = []

        async def on_fc(call: FunctionCall):
            fc_received.append(call)

        session = MockChatSession(responses=[
            create_fc_response("search_products", {"query": "test"}),
            create_text_response("Done"),
        ])
        executor = MockToolExecutor()

        loop = FunctionCallingLoop(
            chat_session=session,
            tool_executor=executor,
            on_function_call=on_fc,
        )

        await loop.execute("test")

        assert len(fc_received) == 1
        assert fc_received[0].name == "search_products"


# =============================================================================
# SUMMARY PROMPT
# =============================================================================

class TestSummaryPrompt:
    """Tests for summary prompt generation."""

    def test_summary_prompt_contains_product_count(self):
        """Test summary prompt includes product count."""
        loop = FunctionCallingLoop(
            chat_session=MockChatSession(),
            tool_executor=MockToolExecutor(),
        )
        loop.state.all_products = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

        prompt = loop._build_summary_prompt()

        assert "3" in prompt
        assert "პროდუქტი" in prompt

    def test_summary_prompt_in_georgian(self):
        """Test summary prompt is in Georgian."""
        loop = FunctionCallingLoop(
            chat_session=MockChatSession(),
            tool_executor=MockToolExecutor(),
        )

        prompt = loop._build_summary_prompt()

        # Should contain Georgian text
        assert "რეკომენდაცია" in prompt or "ქართულად" in prompt
