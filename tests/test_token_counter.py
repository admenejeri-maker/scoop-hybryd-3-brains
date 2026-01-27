"""
Test suite for TokenCounter - Phase 2 of Hybrid Inference Architecture

TDD Step 1: Write failing tests first

TokenCounter estimates token count for context window management.
Key function: Route to extended model when approaching 200k limit.
"""
import pytest
from unittest.mock import patch, MagicMock

# Import will fail initially (TDD - expected)
from app.core.token_counter import TokenCounter


class TestTokenCounterBasics:
    """Basic token counting functionality tests."""

    @pytest.fixture
    def token_counter(self):
        """Create a token counter with default settings."""
        return TokenCounter(
            chars_per_token=4.0,  # English approximation
            extended_threshold=150_000  # Switch at 150k
        )

    def test_empty_string_returns_zero(self, token_counter):
        """Empty string should return 0 tokens."""
        assert token_counter.estimate_tokens("") == 0

    def test_simple_string_estimation(self, token_counter):
        """Simple string should estimate correctly."""
        text = "Hello world"  # 11 chars = ~2.75 tokens
        result = token_counter.estimate_tokens(text)
        assert 2 <= result <= 4  # Reasonable range

    def test_long_text_estimation(self, token_counter):
        """Long text should scale linearly."""
        short_text = "a" * 100
        long_text = "a" * 1000
        
        short_count = token_counter.estimate_tokens(short_text)
        long_count = token_counter.estimate_tokens(long_text)
        
        # Long should be ~10x short
        ratio = long_count / short_count
        assert 9 <= ratio <= 11

    def test_unicode_estimation(self, token_counter):
        """Unicode (Georgian) should use higher chars_per_token."""
        georgian_text = "გამარჯობა"  # Georgian for "Hello"
        result = token_counter.estimate_tokens(georgian_text)
        # Unicode typically uses more tokens per character
        assert result > 0


class TestHistoryTokenCounting:
    """Test counting tokens from conversation history."""

    @pytest.fixture
    def token_counter(self):
        """Create a token counter for history tests."""
        return TokenCounter(
            chars_per_token=4.0,
            extended_threshold=150_000
        )

    def test_empty_history_returns_zero(self, token_counter):
        """Empty history should return 0 tokens."""
        assert token_counter.count_history_tokens([]) == 0

    def test_single_message_history(self, token_counter):
        """Single message history should be counted."""
        history = [
            {"role": "user", "parts": [{"text": "Hello world"}]}
        ]
        result = token_counter.count_history_tokens(history)
        assert result > 0

    def test_multiple_messages_history(self, token_counter):
        """Multiple messages should sum their tokens."""
        history = [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "assistant", "parts": [{"text": "Hi there!"}]},
            {"role": "user", "parts": [{"text": "How are you?"}]}
        ]
        result = token_counter.count_history_tokens(history)
        # Should count all messages
        assert result > 5

    def test_multipart_message(self, token_counter):
        """Messages with multiple parts should sum all parts."""
        history = [
            {"role": "user", "parts": [
                {"text": "Part one"},
                {"text": "Part two"}
            ]}
        ]
        result = token_counter.count_history_tokens(history)
        assert result > 3  # At least the combined text


class TestThresholdDetection:
    """Test detection of context window thresholds."""

    @pytest.fixture
    def token_counter(self):
        """Create a token counter with low threshold for testing."""
        return TokenCounter(
            chars_per_token=4.0,
            extended_threshold=100  # Low threshold for test
        )

    def test_below_threshold_returns_false(self, token_counter):
        """Small history should not exceed threshold."""
        history = [{"role": "user", "parts": [{"text": "Hi"}]}]
        assert token_counter.needs_extended_context(history) is False

    def test_above_threshold_returns_true(self, token_counter):
        """Large history should exceed threshold."""
        history = [{"role": "user", "parts": [{"text": "x" * 500}]}]
        assert token_counter.needs_extended_context(history) is True

    def test_threshold_boundary(self, token_counter):
        """Test exact boundary behavior."""
        # 400 chars = 100 tokens at 4 chars/token
        boundary_text = "x" * 400
        history = [{"role": "user", "parts": [{"text": boundary_text}]}]
        
        # Should be at or just above threshold
        needs_extended = token_counter.needs_extended_context(history)
        assert needs_extended is True


class TestSafetyBuffer:
    """Test safety buffer calculations."""

    @pytest.fixture
    def token_counter(self):
        """Create a token counter with buffer."""
        return TokenCounter(
            chars_per_token=4.0,
            extended_threshold=150_000,
            safety_multiplier=1.2  # 20% buffer
        )

    def test_safety_multiplier_applied(self, token_counter):
        """Safety multiplier should inflate estimates."""
        text = "a" * 400  # 100 tokens without buffer
        result = token_counter.estimate_tokens(text, with_safety_buffer=True)
        expected_base = 100
        expected_buffered = 120  # 100 * 1.2
        assert 115 <= result <= 125  # Allow some variance


class TestTokenCounterMetrics:
    """Test metrics and debugging capabilities."""

    @pytest.fixture
    def token_counter(self):
        """Create a token counter for metrics testing."""
        return TokenCounter(
            chars_per_token=4.0,
            extended_threshold=150_000
        )

    def test_get_breakdown(self, token_counter):
        """Should return detailed token breakdown."""
        history = [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "assistant", "parts": [{"text": "Hi there!"}]},
        ]
        breakdown = token_counter.get_breakdown(history)
        
        assert "total_tokens" in breakdown
        assert "message_count" in breakdown
        assert "per_message" in breakdown
        assert len(breakdown["per_message"]) == 2

    def test_get_context_info(self, token_counter):
        """Should return context utilization info."""
        history = [{"role": "user", "parts": [{"text": "x" * 1000}]}]
        info = token_counter.get_context_info(
            history=history,
            system_prompt_tokens=5000,
            max_context=200_000
        )
        
        assert "history_tokens" in info
        assert "system_tokens" in info
        assert "total_tokens" in info
        assert "utilization_pct" in info
        assert "needs_extended" in info


class TestPerformance:
    """Test performance with large payloads."""

    @pytest.fixture
    def token_counter(self):
        """Create a token counter for performance tests."""
        return TokenCounter(
            chars_per_token=4.0,
            extended_threshold=150_000
        )

    def test_large_payload_under_100ms(self, token_counter):
        """1MB payload should be processed under 100ms."""
        import time
        
        large_text = "x" * 1_000_000  # 1MB
        
        start = time.time()
        result = token_counter.estimate_tokens(large_text)
        elapsed = time.time() - start
        
        assert elapsed < 0.1  # Under 100ms
        assert result > 200_000  # Should be high token count

    def test_large_history_under_100ms(self, token_counter):
        """History with many messages should be fast."""
        import time
        
        # 1000 messages
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", 
             "parts": [{"text": "x" * 100}]}
            for i in range(1000)
        ]
        
        start = time.time()
        result = token_counter.count_history_tokens(history)
        elapsed = time.time() - start
        
        assert elapsed < 0.1  # Under 100ms
        assert result > 20_000  # Should be substantial
