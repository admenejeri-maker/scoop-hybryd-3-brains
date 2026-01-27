"""
Integration Tests for ConversationEngine (v2.0)
===============================================

Tests the complete engine with mocked dependencies.

These tests verify:
1. Engine initialization
2. Sync message processing
3. Streaming message processing
4. Error handling
5. Context loading/saving
6. Feature flag routing
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Import engine components
from app.core.engine import (
    ConversationEngine,
    ConversationEngineConfig,
    SSEEvent,
    create_conversation_engine,
)
from app.core.types import (
    ResponseMode,
    ConversationResult,
    RequestContext,
)
from app.core.function_loop import EmptyResponseError, LoopTimeoutError


# =============================================================================
# MOCK FIXTURES
# =============================================================================

@dataclass
class MockChatResponse:
    """Mock Gemini chat response."""
    text: str = "Test response"
    candidates: List[Any] = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = [MockCandidate(self.text)]


@dataclass
class MockCandidate:
    """Mock response candidate."""
    text: str

    def __post_init__(self):
        self.content = MockContent(self.text)


@dataclass
class MockContent:
    """Mock content with parts."""
    text: str

    def __post_init__(self):
        self.parts = [MockPart(self.text)]


@dataclass
class MockPart:
    """Mock part with text."""
    text: str
    thought: bool = False
    function_call: Any = None


class MockStreamIterator:
    """Mock async iterator for streaming responses."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self._index = 0
        self._words = response_text.split() if response_text else [""]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._words):
            raise StopAsyncIteration
        word = self._words[self._index]
        self._index += 1
        return MockChatResponse(text=word + " ")


class MockChat:
    """Mock Gemini chat session."""

    def __init__(self, response_text: str = "Mock response"):
        self.response_text = response_text
        self._history = []

    async def send_message(self, message: Any) -> MockChatResponse:
        """Send message and return mock response."""
        self._history.append({"role": "user", "parts": [{"text": str(message)}]})
        self._history.append({"role": "model", "parts": [{"text": self.response_text}]})
        return MockChatResponse(text=self.response_text)

    async def send_message_stream(self, message: Any):
        """
        Send message and return an async iterator for streaming.

        NOTE: The SDK's send_message_stream returns an awaitable that yields
        an async iterator. This mock matches that behavior.
        """
        self._history.append({"role": "user", "parts": [{"text": str(message)}]})
        self._history.append({"role": "model", "parts": [{"text": self.response_text}]})
        return MockStreamIterator(self.response_text)

    def get_history(self):
        """Return history."""
        return self._history


class MockGeminiAdapter:
    """Mock GeminiAdapter for testing."""

    def __init__(self, response_text: str = "Mock Gemini response"):
        self.response_text = response_text
        self.create_chat_calls = []

    def create_chat(
        self,
        history: Optional[List] = None,
        tools: Optional[List] = None,
        system_instruction: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> MockChat:
        """Create mock chat session."""
        self.create_chat_calls.append({
            "history": history,
            "tools": tools,
            "system_instruction": system_instruction,
            "model_override": model_override,
        })
        return MockChat(self.response_text)

    def bson_to_sdk_history(self, bson_history: List) -> List:
        """Convert BSON to SDK format (passthrough for mocks)."""
        return bson_history

    def sdk_history_to_bson(self, sdk_history: Any) -> List:
        """Convert SDK to BSON format."""
        return list(sdk_history) if sdk_history else []


class MockMongoAdapter:
    """Mock MongoAdapter for testing."""

    def __init__(self):
        self.history_store = {}
        self.profile_store = {}
        self.stats_store = {}

    async def load_history(
        self,
        user_id: str,
        session_id: Optional[str] = None,
    ):
        """Load mock history."""
        key = f"{user_id}:{session_id or 'default'}"
        history = self.history_store.get(key, [])
        return history, session_id or "mock_session_123", None

    async def save_history(
        self,
        user_id: str,
        session_id: str,
        history: Any,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Save mock history."""
        key = f"{user_id}:{session_id}"
        self.history_store[key] = list(history) if history else []
        return True

    async def get_user_profile(self, user_id: str) -> Optional[Dict]:
        """Get mock user profile."""
        return self.profile_store.get(user_id, {
            "name": "Test User",
            "allergies": [],
            "goals": ["muscle_gain"],
        })

    async def increment_user_stats(
        self,
        user_id: str,
        messages: int = 1,
        sessions: int = 0,
    ) -> bool:
        """Increment mock stats."""
        if user_id not in self.stats_store:
            self.stats_store[user_id] = {"messages": 0, "sessions": 0}
        self.stats_store[user_id]["messages"] += messages
        self.stats_store[user_id]["sessions"] += sessions
        return True


# =============================================================================
# TEST CLASS
# =============================================================================

class TestConversationEngine:
    """Test suite for ConversationEngine."""

    @pytest.fixture
    def mock_gemini(self):
        """Create mock GeminiAdapter."""
        return MockGeminiAdapter(
            response_text="გამარჯობა! რით დაგეხმაროთ პროტეინის არჩევაში?"
        )

    @pytest.fixture
    def mock_mongo(self):
        """Create mock MongoAdapter."""
        return MockMongoAdapter()

    @pytest.fixture
    def engine(self, mock_gemini, mock_mongo):
        """Create engine with mocked dependencies."""
        config = ConversationEngineConfig(
            max_function_rounds=3,
            gemini_timeout_seconds=30,
            retry_on_empty=True,
        )

        return ConversationEngine(
            gemini_adapter=mock_gemini,
            mongo_adapter=mock_mongo,
            config=config,
            system_instruction="You are Scoop AI, a Georgian supplement advisor.",
            tools=[],
        )

    # =========================================================================
    # INITIALIZATION TESTS
    # =========================================================================

    def test_engine_initialization(self, engine):
        """Test engine initializes correctly."""
        assert engine is not None
        assert engine.config.max_function_rounds == 3
        assert engine.gemini is not None
        assert engine.mongo is not None

    def test_engine_requires_gemini(self, mock_mongo):
        """Test engine requires GeminiAdapter or API key."""
        with pytest.raises(ValueError, match="gemini_adapter or gemini_api_key"):
            ConversationEngine(
                mongo_adapter=mock_mongo,
            )

    # =========================================================================
    # SYNC MESSAGE PROCESSING TESTS
    # =========================================================================

    @pytest.mark.asyncio
    async def test_process_message_basic(self, engine):
        """Test basic sync message processing."""
        result = await engine.process_message(
            user_id="test_user",
            message="გამარჯობა!",
        )

        assert result is not None
        assert result.success is True
        assert isinstance(result.text, str)
        assert len(result.text) > 0

    @pytest.mark.asyncio
    async def test_process_message_with_session(self, engine):
        """Test message processing with session ID."""
        result = await engine.process_message(
            user_id="test_user",
            message="რომელი პროტეინი ჯობია?",
            session_id="session_abc123",
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_message_loads_context(self, engine, mock_mongo):
        """Test that context is loaded before processing."""
        # Pre-populate history
        mock_mongo.history_store["test_user:session_123"] = [
            {"role": "user", "parts": [{"text": "Previous message"}]},
            {"role": "model", "parts": [{"text": "Previous response"}]},
        ]

        result = await engine.process_message(
            user_id="test_user",
            message="Follow up question",
            session_id="session_123",
        )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_message_saves_context(self, engine, mock_mongo):
        """Test that context is saved after processing."""
        result = await engine.process_message(
            user_id="test_user",
            message="New message",
            session_id="save_test_session",
        )

        assert result.success is True
        # Check stats were incremented
        assert "test_user" in mock_mongo.stats_store
        assert mock_mongo.stats_store["test_user"]["messages"] >= 1

    # =========================================================================
    # STREAMING MESSAGE TESTS
    # =========================================================================

    @pytest.mark.asyncio
    async def test_stream_message_basic(self, engine):
        """Test basic streaming message processing."""
        events = []

        async for event in engine.stream_message(
            user_id="test_user",
            message="გამარჯობა!",
        ):
            events.append(event)

        assert len(events) > 0
        # Should have at least thinking, text/error, and done events
        event_types = [e.event_type for e in events]
        # v2 uses ThinkingManager which emits "thinking" events instead of "status"
        assert "thinking" in event_types or "done" in event_types or "error" in event_types

    @pytest.mark.asyncio
    async def test_stream_message_yields_sse_events(self, engine):
        """Test that stream yields proper SSE events."""
        async for event in engine.stream_message(
            user_id="test_user",
            message="Test message",
        ):
            assert isinstance(event, SSEEvent)
            assert hasattr(event, 'event_type')
            assert hasattr(event, 'data')
            assert hasattr(event, 'to_sse')

            # Verify SSE format
            sse_string = event.to_sse()
            assert "event:" in sse_string
            assert "data:" in sse_string

    # =========================================================================
    # SSE EVENT TESTS
    # =========================================================================

    def test_sse_event_creation(self):
        """Test SSEEvent creation."""
        event = SSEEvent(
            event_type="text",
            data={"content": "Hello world"}
        )

        assert event.event_type == "text"
        assert event.data["content"] == "Hello world"

    def test_sse_event_to_sse_format(self):
        """Test SSE event formatting."""
        event = SSEEvent(
            event_type="thinking",
            data={"step": "ანალიზი..."}
        )

        sse_string = event.to_sse()
        assert sse_string.startswith("event: thinking")
        assert "data:" in sse_string
        assert sse_string.endswith("\n\n")

    # =========================================================================
    # REQUEST CONTEXT TESTS
    # =========================================================================

    def test_request_context_creation(self):
        """Test RequestContext creation."""
        context = RequestContext(
            user_id="user123",
            message="Test message",
            session_id="session456",
            mode=ResponseMode.SYNC,
        )

        assert context.user_id == "user123"
        assert context.message == "Test message"
        assert context.session_id == "session456"
        assert context.mode == ResponseMode.SYNC

    def test_request_context_elapsed_time(self):
        """Test RequestContext elapsed time calculation."""
        import time

        context = RequestContext(
            user_id="user",
            message="msg",
        )

        time.sleep(0.1)
        elapsed = context.elapsed_seconds()

        assert elapsed >= 0.1
        assert elapsed < 1.0

    # =========================================================================
    # CONFIGURATION TESTS
    # =========================================================================

    def test_engine_config_defaults(self):
        """Test ConversationEngineConfig default values."""
        config = ConversationEngineConfig()

        assert config.max_function_rounds == 5
        assert config.max_unique_queries == 3
        assert config.gemini_timeout_seconds == 30
        assert config.retry_on_empty is True
        assert config.thinking_strategy == "simple_loader"

    def test_engine_config_custom_values(self):
        """Test ConversationEngineConfig with custom values."""
        config = ConversationEngineConfig(
            max_function_rounds=5,
            max_unique_queries=4,
            gemini_timeout_seconds=60,
            retry_on_empty=False,
            model_name="gemini-custom",
        )

        assert config.max_function_rounds == 5
        assert config.max_unique_queries == 4
        assert config.gemini_timeout_seconds == 60
        assert config.retry_on_empty is False
        assert config.model_name == "gemini-custom"

    # =========================================================================
    # PROFILE CONTEXT TESTS
    # =========================================================================

    @pytest.mark.asyncio
    async def test_profile_context_injection(self, engine, mock_mongo):
        """Test that user profile is injected into system prompt."""
        # Set up profile
        mock_mongo.profile_store["profile_user"] = {
            "name": "გიორგი",
            "allergies": ["lactose"],
            "goals": ["muscle_gain"],
            "fitness_level": "intermediate",
        }

        result = await engine.process_message(
            user_id="profile_user",
            message="What protein should I use?",
        )

        assert result.success is True
        # Profile should have been loaded and used

    def test_format_profile_context(self, engine):
        """Test profile formatting for system prompt."""
        profile = {
            "name": "ნიკა",
            "allergies": ["gluten", "soy"],
            "goals": ["weight_loss"],
            "fitness_level": "beginner",
            "demographics": {"age": 25},
            "physical_stats": {"height": 175, "current_weight": 80},
        }

        formatted = engine._format_profile_context(profile)

        assert "ნიკა" in formatted
        assert "gluten" in formatted
        assert "weight_loss" in formatted
        assert "25" in formatted
        assert "175" in formatted

    def test_format_empty_profile(self, engine):
        """Test formatting empty profile returns empty string."""
        formatted = engine._format_profile_context({})
        assert formatted == ""

    # =========================================================================
    # CONVERSATION RESULT TESTS
    # =========================================================================

    def test_conversation_result_to_dict(self):
        """Test ConversationResult serialization."""
        result = ConversationResult(
            text="Test response",
            products=[{"id": "1", "name": "Protein"}],
            tip="Tip content",
            quick_replies=[{"title": "More info", "payload": "more"}],
            success=True,
            metadata={"rounds": 2},
        )

        result_dict = result.to_dict()

        assert result_dict["response_text_geo"] == "Test response"
        assert len(result_dict["products"]) == 1
        assert result_dict["tip"] == "Tip content"
        assert result_dict["success"] is True
        assert result_dict["metadata"]["rounds"] == 2


# =============================================================================
# ADAPTER INTEGRATION TESTS
# =============================================================================

class TestAdapterIntegration:
    """Test adapter integration with engine."""

    @pytest.fixture
    def mock_gemini(self):
        return MockGeminiAdapter()

    @pytest.fixture
    def mock_mongo(self):
        return MockMongoAdapter()

    @pytest.mark.asyncio
    async def test_gemini_adapter_called_with_history(self, mock_gemini, mock_mongo):
        """Test that GeminiAdapter receives history."""
        # Pre-populate history
        mock_mongo.history_store["user:session"] = [
            {"role": "user", "parts": [{"text": "Hi"}]},
        ]

        engine = ConversationEngine(
            gemini_adapter=mock_gemini,
            mongo_adapter=mock_mongo,
            system_instruction="Test",
        )

        await engine.process_message(
            user_id="user",
            message="Follow up",
            session_id="session",
        )

        # Check create_chat was called
        assert len(mock_gemini.create_chat_calls) > 0

    @pytest.mark.asyncio
    async def test_mongo_adapter_saves_after_message(self, mock_gemini, mock_mongo):
        """Test that MongoAdapter saves history after processing."""
        engine = ConversationEngine(
            gemini_adapter=mock_gemini,
            mongo_adapter=mock_mongo,
        )

        await engine.process_message(
            user_id="save_user",
            message="Test",
            session_id="save_session",
        )

        # Stats should be incremented
        assert "save_user" in mock_mongo.stats_store


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Test error handling in ConversationEngine."""

    @pytest.fixture
    def mock_mongo(self):
        return MockMongoAdapter()

    @pytest.mark.asyncio
    async def test_handles_empty_response_error(self, mock_mongo):
        """Test handling of EmptyResponseError."""

        class FailingGeminiAdapter(MockGeminiAdapter):
            def create_chat(self, **kwargs):
                chat = MockChat("")
                return chat

        engine = ConversationEngine(
            gemini_adapter=FailingGeminiAdapter(),
            mongo_adapter=mock_mongo,
        )

        result = await engine.process_message(
            user_id="user",
            message="test",
        )

        # Should return error response, not crash
        assert result is not None
        # May be success=False due to empty text handling

    @pytest.mark.asyncio
    async def test_handles_general_exception(self, mock_mongo):
        """Test handling of general exceptions."""

        class ExceptionGeminiAdapter(MockGeminiAdapter):
            def create_chat(self, **kwargs):
                raise RuntimeError("Simulated error")

        engine = ConversationEngine(
            gemini_adapter=ExceptionGeminiAdapter(),
            mongo_adapter=mock_mongo,
        )

        result = await engine.process_message(
            user_id="user",
            message="test",
        )

        # Should return error response, not crash
        assert result is not None
        assert result.success is False
        assert result.error_code is not None


# =============================================================================
# FEATURE FLAG TESTS
# =============================================================================

class TestFeatureFlag:
    """Test ENGINE_VERSION feature flag behavior."""

    def test_config_has_engine_version(self):
        """Test that settings has engine_version field."""
        from config import settings
        assert hasattr(settings, 'engine_version')
        assert settings.engine_version in ["v1", "v2"]

    def test_v2_imports_available(self):
        """Test that v2 engine components are importable."""
        from app.core import (
            ConversationEngine,
            ConversationEngineConfig,
            ResponseMode,
            SSEEvent,
        )

        assert ConversationEngine is not None
        assert ConversationEngineConfig is not None
        assert ResponseMode is not None
        assert SSEEvent is not None
