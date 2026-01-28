"""
Tests for ThinkingManager (v2.0)
================================

Tests the Thinking UI strategy pattern implementation.
"""

import sys
import os

# Add backend root to path for app imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
import asyncio
from unittest.mock import MagicMock

from app.core.thinking_manager import (
    ThinkingManager,
    ThinkingStrategy,
    ThinkingEvent,
    create_thinking_manager,
    thinking_event_generator,
    CATEGORY_THINKING_MESSAGES,
    INTENT_KEYWORDS,
)


# =============================================================================
# THINKING STRATEGY TESTS
# =============================================================================

class TestThinkingStrategy:
    """Tests for ThinkingStrategy enum."""

    def test_strategy_values(self):
        """Test strategy enum values."""
        assert ThinkingStrategy.NONE.value == "none"
        assert ThinkingStrategy.SIMPLE_LOADER.value == "simple_loader"
        assert ThinkingStrategy.NATIVE.value == "native"

    def test_strategy_from_string(self):
        """Test creating strategy from string."""
        assert ThinkingStrategy("none") == ThinkingStrategy.NONE
        assert ThinkingStrategy("simple_loader") == ThinkingStrategy.SIMPLE_LOADER
        assert ThinkingStrategy("native") == ThinkingStrategy.NATIVE


# =============================================================================
# THINKING EVENT TESTS
# =============================================================================

class TestThinkingEvent:
    """Tests for ThinkingEvent dataclass."""

    def test_event_creation(self):
        """Test basic event creation."""
        event = ThinkingEvent(
            content="ვფიქრობ...",
            step=1,
            is_final=False,
        )

        assert event.content == "ვფიქრობ..."
        assert event.step == 1
        assert event.is_final is False

    def test_event_to_sse_data(self):
        """Test event SSE data conversion."""
        event = ThinkingEvent(
            content="ვეძებ პროდუქტებს...",
            step=2,
            is_final=False,
        )

        data = event.to_sse_data()

        assert data["type"] == "thinking"
        assert data["content"] == "ვეძებ პროდუქტებს..."
        assert data["step"] == 2
        assert data["is_final"] is False

    def test_event_final_flag(self):
        """Test final event flag."""
        event = ThinkingEvent(
            content="მზადაა!",
            step=3,
            is_final=True,
        )

        data = event.to_sse_data()
        assert data["is_final"] is True


# =============================================================================
# THINKING MANAGER INITIALIZATION TESTS
# =============================================================================

class TestThinkingManagerInit:
    """Tests for ThinkingManager initialization."""

    def test_default_initialization(self):
        """Test default initialization."""
        manager = ThinkingManager()

        assert manager.strategy == ThinkingStrategy.SIMPLE_LOADER
        assert manager.step_count == 0
        assert manager.is_complete is False

    def test_strategy_none(self):
        """Test NONE strategy initialization."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NONE)
        assert manager.strategy == ThinkingStrategy.NONE

    def test_strategy_native(self):
        """Test NATIVE strategy initialization."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NATIVE)
        assert manager.strategy == ThinkingStrategy.NATIVE

    def test_custom_messages(self):
        """Test custom messages initialization."""
        custom = ["Step 1", "Step 2"]
        manager = ThinkingManager(
            strategy=ThinkingStrategy.SIMPLE_LOADER,
            custom_messages=custom,
        )

        assert manager.custom_messages == custom


# =============================================================================
# INITIAL EVENTS TESTS
# =============================================================================

class TestInitialEvents:
    """Tests for get_initial_events method."""

    def test_none_strategy_returns_empty(self):
        """Test NONE strategy returns no initial events."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NONE)
        events = manager.get_initial_events("რომელი პროტეინი ჯობია?")

        assert events == []

    def test_native_strategy_returns_empty(self):
        """Test NATIVE strategy returns no initial events."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NATIVE)
        events = manager.get_initial_events("რომელი პროტეინი ჯობია?")

        assert events == []

    def test_simple_loader_returns_events(self):
        """Test SIMPLE_LOADER returns thinking events."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)
        events = manager.get_initial_events("რომელი პროტეინი ჯობია?")

        assert len(events) > 0
        assert all(isinstance(e, ThinkingEvent) for e in events)

    def test_events_have_incremental_steps(self):
        """Test events have incremental step numbers."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)
        events = manager.get_initial_events("ძებნა პროტეინი")

        steps = [e.step for e in events]
        assert steps == sorted(steps)
        assert steps[0] >= 1

    def test_custom_messages_override(self):
        """Test custom messages are used."""
        custom = ["Custom Step 1", "Custom Step 2"]
        manager = ThinkingManager(
            strategy=ThinkingStrategy.SIMPLE_LOADER,
            custom_messages=custom,
        )

        events = manager.get_initial_events("test message")

        contents = [e.content for e in events]
        assert contents == custom


# =============================================================================
# INTENT DETECTION TESTS
# =============================================================================

class TestIntentDetection:
    """Tests for intent detection."""

    def test_search_intent_detected(self):
        """Test search intent detection."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        events = manager.get_initial_events("მოძებნე პროტეინი")

        # Should use search category messages
        assert any("ეძებ" in e.content for e in events)

    def test_recommendation_intent_detected(self):
        """Test recommendation intent detection."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        events = manager.get_initial_events("რომელი პროტეინი ჯობია?")

        # Should have recommendation-related content
        assert len(events) > 0

    def test_general_intent_fallback(self):
        """Test general intent fallback."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        events = manager.get_initial_events("გამარჯობა")

        # Should use general category
        assert len(events) > 0


# =============================================================================
# FUNCTION CALL EVENT TESTS
# =============================================================================

class TestFunctionCallEvents:
    """Tests for function call events."""

    def test_function_call_event_search(self):
        """Test function call event for search_products."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)
        event = manager.get_function_call_event("search_products")

        assert event is not None
        assert "ეძებ" in event.content
        assert event.is_final is False

    def test_function_call_event_profile(self):
        """Test function call event for get_user_profile."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)
        event = manager.get_function_call_event("get_user_profile")

        assert event is not None
        assert "პროფილ" in event.content

    def test_function_call_event_unknown(self):
        """Test function call event for unknown function."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)
        event = manager.get_function_call_event("unknown_function")

        assert event is not None
        assert "unknown_function" in event.content

    def test_none_strategy_no_function_events(self):
        """Test NONE strategy returns None for function events."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NONE)
        event = manager.get_function_call_event("search_products")

        assert event is None


# =============================================================================
# RETRY EVENT TESTS
# =============================================================================

class TestRetryEvents:
    """Tests for retry scenario events."""

    def test_retry_event_creation(self):
        """Test retry event creation."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)
        event = manager.get_retry_event(5)

        assert event is not None
        assert "5" in event.content
        assert "პროდუქტი" in event.content

    def test_retry_event_increments_step(self):
        """Test retry event increments step count."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        initial_step = manager.step_count
        manager.get_retry_event(3)

        assert manager.step_count == initial_step + 1


# =============================================================================
# COMPLETION EVENT TESTS
# =============================================================================

class TestCompletionEvents:
    """Tests for completion events."""

    def test_completion_event(self):
        """Test completion event creation."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)
        event = manager.get_completion_event()

        assert event is not None
        assert event.is_final is True
        assert "მზადაა" in event.content

    def test_completion_marks_complete(self):
        """Test completion marks manager as complete."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        assert manager.is_complete is False
        manager.get_completion_event()
        assert manager.is_complete is True

    def test_second_completion_returns_none(self):
        """Test second completion request returns None."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        first = manager.get_completion_event()
        second = manager.get_completion_event()

        assert first is not None
        assert second is None

    def test_none_strategy_no_completion_event(self):
        """Test NONE strategy returns None for completion."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NONE)
        event = manager.get_completion_event()

        assert event is None


# =============================================================================
# NATIVE THOUGHT PROCESSING TESTS
# =============================================================================

class TestNativeThoughtProcessing:
    """Tests for NATIVE mode thought processing."""

    def test_process_thought_non_native_returns_none(self):
        """Test non-NATIVE strategy ignores thought parts."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        mock_part = MagicMock()
        mock_part.thought = True
        mock_part.text = "Thinking about proteins..."

        event = manager.process_thought_part(mock_part)

        assert event is None

    def test_process_thought_native_mode(self):
        """Test NATIVE mode processes thought parts."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NATIVE)

        mock_part = MagicMock()
        mock_part.thought = True
        mock_part.text = "Analyzing user preferences..."

        event = manager.process_thought_part(mock_part)

        assert event is not None
        assert event.content == "Analyzing user preferences..."

    def test_process_non_thought_part(self):
        """Test non-thought parts are ignored in NATIVE mode."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NATIVE)

        mock_part = MagicMock()
        mock_part.thought = False
        mock_part.text = "Regular text"

        event = manager.process_thought_part(mock_part)

        assert event is None

    def test_native_buffers_thoughts(self):
        """Test NATIVE mode buffers thought texts."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NATIVE)

        mock_part1 = MagicMock()
        mock_part1.thought = True
        mock_part1.text = "Thought 1"

        mock_part2 = MagicMock()
        mock_part2.thought = True
        mock_part2.text = "Thought 2"

        manager.process_thought_part(mock_part1)
        manager.process_thought_part(mock_part2)

        assert len(manager.thought_buffer) == 2
        assert "Thought 1" in manager.thought_buffer
        assert "Thought 2" in manager.thought_buffer


# =============================================================================
# STATE MANAGEMENT TESTS
# =============================================================================

class TestStateManagement:
    """Tests for state management."""

    def test_reset_clears_state(self):
        """Test reset clears all state."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        # Generate some events
        manager.get_initial_events("test")
        manager.get_completion_event()

        assert manager.step_count > 0
        assert manager.is_complete is True

        # Reset
        manager.reset()

        assert manager.step_count == 0
        assert manager.is_complete is False
        assert manager.thought_buffer == []

    def test_mark_complete(self):
        """Test mark_complete method."""
        manager = ThinkingManager()

        manager.mark_complete()

        assert manager.is_complete is True


# =============================================================================
# FACTORY FUNCTION TESTS
# =============================================================================

class TestFactoryFunction:
    """Tests for create_thinking_manager factory."""

    def test_default_factory(self):
        """Test factory with defaults."""
        manager = create_thinking_manager()

        assert manager is not None
        # Default is SIMPLE_LOADER
        assert manager.strategy == ThinkingStrategy.SIMPLE_LOADER

    def test_factory_with_strategy(self):
        """Test factory with strategy parameter."""
        manager = create_thinking_manager(strategy="none")
        assert manager.strategy == ThinkingStrategy.NONE

        manager = create_thinking_manager(strategy="simple_loader")
        assert manager.strategy == ThinkingStrategy.SIMPLE_LOADER

        manager = create_thinking_manager(strategy="native")
        assert manager.strategy == ThinkingStrategy.NATIVE

    def test_factory_with_invalid_strategy(self):
        """Test factory with invalid strategy falls back."""
        manager = create_thinking_manager(strategy="invalid_strategy")
        # Should fall back to default
        assert manager.strategy == ThinkingStrategy.SIMPLE_LOADER

    def test_factory_with_custom_messages(self):
        """Test factory with custom messages."""
        custom = ["Custom 1", "Custom 2"]
        manager = create_thinking_manager(custom_messages=custom)

        assert manager.custom_messages == custom


# =============================================================================
# ASYNC GENERATOR TESTS
# =============================================================================

class TestAsyncGenerator:
    """Tests for async event generator."""

    @pytest.mark.asyncio
    async def test_generator_yields_events(self):
        """Test async generator yields events."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        events = []
        async for event in thinking_event_generator(
            manager,
            "test message",
            delay_seconds=0,  # No delay for testing
        ):
            events.append(event)

        assert len(events) > 0
        assert all(isinstance(e, ThinkingEvent) for e in events)

    @pytest.mark.asyncio
    async def test_generator_respects_delay(self):
        """Test async generator respects delay."""
        import time

        manager = ThinkingManager(
            strategy=ThinkingStrategy.SIMPLE_LOADER,
            custom_messages=["Step 1", "Step 2"],
        )

        start = time.time()
        events = []
        async for event in thinking_event_generator(
            manager,
            "test",
            delay_seconds=0.1,
        ):
            events.append(event)
        elapsed = time.time() - start

        # Should have delays between events (but not after last)
        assert elapsed >= 0.1  # At least one delay

    @pytest.mark.asyncio
    async def test_generator_empty_for_none_strategy(self):
        """Test generator yields nothing for NONE strategy."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NONE)

        events = []
        async for event in thinking_event_generator(manager, "test"):
            events.append(event)

        assert events == []


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestIntegration:
    """Integration tests for ThinkingManager."""

    def test_full_simple_loader_flow(self):
        """Test complete SIMPLE_LOADER flow."""
        manager = ThinkingManager(strategy=ThinkingStrategy.SIMPLE_LOADER)

        # Initial events
        initial = manager.get_initial_events("მოძებნე პროტეინი")
        assert len(initial) > 0

        # Function call event
        fc_event = manager.get_function_call_event("search_products")
        assert fc_event is not None

        # Completion
        completion = manager.get_completion_event()
        assert completion is not None
        assert completion.is_final is True

        # Verify step count
        assert manager.step_count >= len(initial) + 2  # +2 for fc and completion

    def test_full_native_flow(self):
        """Test complete NATIVE flow."""
        manager = ThinkingManager(strategy=ThinkingStrategy.NATIVE)

        # Initial events (should be empty for NATIVE)
        initial = manager.get_initial_events("test")
        assert initial == []

        # Process some thoughts
        mock_part = MagicMock()
        mock_part.thought = True
        mock_part.text = "Thinking..."

        event = manager.process_thought_part(mock_part)
        assert event is not None

        # Completion still works
        completion = manager.get_completion_event()
        assert completion is not None
