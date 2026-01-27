"""
Tests for Hybrid Inference Manager
==================================

Integration tests for HybridInferenceManager orchestration:
1. Routing decisions
2. Success/failure recording
3. Circuit breaker integration
4. Fallback trigger integration
5. Metrics aggregation
"""

import pytest
from unittest.mock import MagicMock, patch

from app.core.hybrid_manager import (
    HybridInferenceManager,
    HybridConfig,
    InferenceMetrics,
)
# CircuitBreaker uses string states: "CLOSED", "OPEN", "HALF_OPEN"
from app.core.fallback_trigger import FallbackReason


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def manager():
    """Create manager with default config."""
    config = HybridConfig(
        primary_model="gemini-3-flash-preview",
        fallback_model="gemini-2.5-flash",
        extended_model="gemini-2.5-pro",
        circuit_failure_threshold=3,
        circuit_recovery_seconds=60.0,
        extended_context_threshold=100000,
    )
    return HybridInferenceManager(config=config)


# =============================================================================
# ROUTING TESTS
# =============================================================================

class TestRouting:
    """Test request routing."""
    
    def test_routes_to_primary_by_default(self, manager):
        """Short messages route to primary model."""
        result = manager.route_request(
            message="Hello",
            history=[],
        )
        
        assert result.model == "gemini-3-flash-preview"
        assert manager._metrics.total_requests == 1
    
    def test_routes_to_extended_for_long_context(self, manager):
        """Long context routes to extended model."""
        # Create large history that exceeds 100k threshold
        # Each char ≈ 0.25 tokens, so need ~400k chars for 100k tokens
        large_history = [
            {"role": "user", "parts": [{"text": "x" * 200000}]},
            {"role": "model", "parts": [{"text": "y" * 200000}]},
        ]
        
        result = manager.route_request(
            message="Continue",
            history=large_history,
        )
        
        assert result.model == "gemini-2.5-pro"
        assert manager._metrics.extended_uses == 1
    
    def test_force_fallback(self, manager):
        """Force fallback flag works."""
        result = manager.route_request(
            message="Test",
            history=[],
            force_fallback=True,
        )
        
        assert result.model == "gemini-2.5-flash"
        assert manager._metrics.fallback_uses == 1


# =============================================================================
# SUCCESS/FAILURE RECORDING
# =============================================================================

class TestResultRecording:
    """Test success and failure recording."""
    
    def test_record_success_updates_metrics(self, manager):
        """Success updates metrics."""
        manager.route_request(message="Test", history=[])
        manager.record_success()
        
        assert manager._metrics.primary_successes == 1
    
    def test_record_failure_triggers_retry(self, manager):
        """First failure suggests retry."""
        manager.route_request(message="Test", history=[])
        
        # 503 error is retryable
        exception = Exception("503 Service Unavailable")
        should_retry, fallback = manager.record_failure(exception=exception)
        
        assert should_retry is True
        assert fallback is None
        assert manager._metrics.retries == 1
    
    def test_non_retryable_failure_triggers_fallback(self, manager):
        """Non-retryable failure goes to fallback."""
        manager.route_request(message="Test", history=[])
        
        # Safety block is not retryable
        exception = Exception("SAFETY policy violated")
        should_retry, fallback = manager.record_failure(exception=exception)
        
        assert should_retry is False
        assert fallback is not None
        assert fallback.model == "gemini-2.5-flash"
        assert manager._metrics.safety_blocks == 1


# =============================================================================
# CIRCUIT BREAKER INTEGRATION
# =============================================================================

class TestCircuitBreakerIntegration:
    """Test circuit breaker integration."""
    
    def test_circuit_opens_after_threshold(self, manager):
        """Circuit opens after enough failures."""
        # Record failures to trip the circuit
        for _ in range(3):  # threshold is 3
            manager.record_failure(exception=Exception("503 error"))
        
        # Circuit should be open now
        assert manager.circuit_state == "OPEN"
        assert manager.is_healthy is False
    
    def test_open_circuit_routes_to_fallback(self, manager):
        """Open circuit automatically routes to fallback."""
        # Trip the circuit
        for _ in range(3):
            manager.circuit_breaker.record_failure()
        
        # New routing should go to fallback
        result = manager.route_request(message="Test", history=[])
        
        assert result.model == "gemini-2.5-flash"


# =============================================================================
# STATUS AND METRICS
# =============================================================================

class TestStatusAndMetrics:
    """Test status and metrics reporting."""
    
    def test_get_status_returns_all_components(self, manager):
        """Status includes all component states."""
        status = manager.get_status()
        
        assert "circuit_breaker" in status
        assert "token_counter" in status
        assert "model_router" in status
        assert "fallback_trigger" in status
        assert "manager_metrics" in status
    
    def test_metrics_track_correctly(self, manager):
        """Metrics increment correctly."""
        # Route some requests
        manager.route_request(message="Test1", history=[])
        manager.record_success()
        
        manager.route_request(message="Test2", history=[], force_fallback=True)
        
        metrics = manager.get_metrics()
        
        assert metrics["total_requests"] == 2
        assert metrics["primary_successes"] == 1
        assert metrics["fallback_uses"] == 1
    
    def test_reset_metrics(self, manager):
        """Reset clears all metrics."""
        manager.route_request(message="Test", history=[])
        manager.record_success()
        
        manager.reset_metrics()
        
        metrics = manager.get_metrics()
        assert metrics["total_requests"] == 0
        assert metrics["primary_successes"] == 0


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Test edge cases."""
    
    def test_failure_with_no_exception_or_response(self, manager):
        """Handle failure with no details."""
        should_retry, fallback = manager.record_failure()
        
        # Should still work (unknown error)
        assert should_retry is True or fallback is not None
    
    def test_multiple_retries_then_fallback(self, manager):
        """After max retries, falls back."""
        manager.config.max_retries = 2
        
        # First retry
        should_retry, _ = manager.record_failure(
            exception=Exception("503 error")
        )
        assert should_retry is True
        
        # Second retry
        should_retry, _ = manager.record_failure(
            exception=Exception("503 error")
        )
        assert should_retry is True
        
        # Third should fallback
        should_retry, fallback = manager.record_failure(
            exception=Exception("503 error")
        )
        # Either exhausted retries or falling back
        assert fallback is not None or should_retry is False
