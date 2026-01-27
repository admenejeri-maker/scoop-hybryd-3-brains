"""
Test suite for CircuitBreaker - Phase 1 of Hybrid Inference Architecture

TDD Step 1: Write failing tests first
"""
import asyncio
import pytest
import time
from unittest.mock import AsyncMock, patch

# Import will fail initially (TDD - expected)
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen


class TestCircuitBreakerBasics:
    """Basic circuit breaker functionality tests."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a circuit breaker with test settings."""
        return CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=5.0,
            name="test_circuit"
        )

    def test_initial_state_closed(self, circuit_breaker):
        """Circuit breaker should start in CLOSED state."""
        assert circuit_breaker.state == "CLOSED"
        assert circuit_breaker.failure_count == 0

    def test_single_failure_stays_closed(self, circuit_breaker):
        """Single failure should not open circuit."""
        circuit_breaker.record_failure()
        assert circuit_breaker.state == "CLOSED"
        assert circuit_breaker.failure_count == 1

    def test_threshold_failures_opens_circuit(self, circuit_breaker):
        """Reaching threshold should open circuit."""
        for _ in range(3):
            circuit_breaker.record_failure()
        assert circuit_breaker.state == "OPEN"
        assert circuit_breaker.is_open

    def test_success_resets_failures(self, circuit_breaker):
        """Success should reset failure count."""
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()
        circuit_breaker.record_success()
        assert circuit_breaker.failure_count == 0

    def test_open_circuit_raises_exception(self, circuit_breaker):
        """Open circuit should raise CircuitBreakerOpen."""
        for _ in range(3):
            circuit_breaker.record_failure()
        
        with pytest.raises(CircuitBreakerOpen):
            circuit_breaker.check_state()


class TestCircuitBreakerRecovery:
    """Test recovery behavior after circuit opens."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a circuit breaker with short recovery timeout."""
        return CircuitBreaker(
            failure_threshold=2,
            recovery_timeout=0.1,  # 100ms for fast tests
            name="recovery_test"
        )

    def test_half_open_after_timeout(self, circuit_breaker):
        """Circuit should enter HALF_OPEN after recovery timeout."""
        # Open the circuit
        for _ in range(2):
            circuit_breaker.record_failure()
        assert circuit_breaker.state == "OPEN"
        
        # Wait for recovery timeout
        time.sleep(0.15)
        
        # Should transition to HALF_OPEN
        assert circuit_breaker.state == "HALF_OPEN"

    def test_half_open_success_closes_circuit(self, circuit_breaker):
        """Success in HALF_OPEN should close circuit."""
        # Open the circuit
        for _ in range(2):
            circuit_breaker.record_failure()
        
        # Wait for recovery timeout
        time.sleep(0.15)
        assert circuit_breaker.state == "HALF_OPEN"
        
        # Success should close circuit
        circuit_breaker.record_success()
        assert circuit_breaker.state == "CLOSED"
        assert circuit_breaker.failure_count == 0

    def test_half_open_failure_reopens_circuit(self, circuit_breaker):
        """Failure in HALF_OPEN should reopen circuit."""
        # Open the circuit
        for _ in range(2):
            circuit_breaker.record_failure()
        
        # Wait for recovery timeout
        time.sleep(0.15)
        assert circuit_breaker.state == "HALF_OPEN"
        
        # Failure should reopen circuit
        circuit_breaker.record_failure()
        assert circuit_breaker.state == "OPEN"


class TestCircuitBreakerErrorTypes:
    """Test specific error types that trigger circuit breaker."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a circuit breaker for error type testing."""
        return CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=60.0,
            name="error_type_test"
        )

    def test_503_error_triggers_failure(self, circuit_breaker):
        """503 ServiceUnavailable should count as failure."""
        circuit_breaker.record_failure(error_type="ServiceUnavailable")
        assert circuit_breaker.failure_count == 1

    def test_500_error_triggers_failure(self, circuit_breaker):
        """500 InternalServerError should count as failure."""
        circuit_breaker.record_failure(error_type="InternalServerError")
        assert circuit_breaker.failure_count == 1

    def test_429_error_triggers_failure(self, circuit_breaker):
        """429 ResourceExhausted should count as failure."""
        circuit_breaker.record_failure(error_type="ResourceExhausted")
        assert circuit_breaker.failure_count == 1

    def test_safety_trigger_counts_as_failure(self, circuit_breaker):
        """SAFETY finish reason should count as failure."""
        circuit_breaker.record_failure(error_type="SAFETY_FINISH")
        assert circuit_breaker.failure_count == 1

    def test_invalid_argument_counts_as_failure(self, circuit_breaker):
        """InvalidArgument (400) should count as failure."""
        circuit_breaker.record_failure(error_type="InvalidArgument")
        assert circuit_breaker.failure_count == 1


class TestCircuitBreakerMetrics:
    """Test metrics and monitoring capabilities."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a circuit breaker for metrics testing."""
        return CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
            name="metrics_test"
        )

    def test_get_metrics(self, circuit_breaker):
        """Should return metrics dictionary."""
        circuit_breaker.record_failure()
        circuit_breaker.record_success()
        circuit_breaker.record_failure()
        
        metrics = circuit_breaker.get_metrics()
        
        assert "state" in metrics
        assert "failure_count" in metrics
        assert "total_failures" in metrics
        assert "total_successes" in metrics
        assert "last_failure_time" in metrics
        assert metrics["total_failures"] == 2
        assert metrics["total_successes"] == 1

    def test_failure_window_cleanup(self, circuit_breaker):
        """Old failures should be cleaned up."""
        # Record failures at different times
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()
        
        # Simulate old failures being cleaned (time window)
        old_failures = circuit_breaker.clean_old_failures(window_seconds=0)
        
        # After cleanup with 0 window, all should be removed
        assert circuit_breaker.failure_count == 0


class TestCircuitBreakerConcurrency:
    """Test thread-safety under concurrent access."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a circuit breaker for concurrency testing."""
        return CircuitBreaker(
            failure_threshold=100,  # High threshold for stress test
            recovery_timeout=60.0,
            name="concurrency_test"
        )

    @pytest.mark.asyncio
    async def test_concurrent_failures(self, circuit_breaker):
        """Circuit breaker should handle concurrent failures safely."""
        async def record_failure():
            circuit_breaker.record_failure()

        # Run 50 concurrent failure recordings
        tasks = [record_failure() for _ in range(50)]
        await asyncio.gather(*tasks)

        # All failures should be recorded (no race condition)
        assert circuit_breaker.failure_count == 50

    @pytest.mark.asyncio
    async def test_concurrent_mixed_operations(self, circuit_breaker):
        """Circuit breaker should handle mixed operations safely."""
        async def record_failure():
            circuit_breaker.record_failure()

        async def record_success():
            circuit_breaker.record_success()

        # Run mixed concurrent operations
        tasks = [record_failure() for _ in range(30)]
        tasks.extend([record_success() for _ in range(20)])
        await asyncio.gather(*tasks)

        # Should have handled all operations
        metrics = circuit_breaker.get_metrics()
        assert metrics["total_failures"] == 30
        assert metrics["total_successes"] == 20
