"""
CircuitBreaker - Phase 1 of Hybrid Inference Architecture

Implements the Circuit Breaker pattern for Gemini API call protection.
Based on 16-point weakness mitigation framework from challenge analysis.

Key Mitigations:
- W1: Preview Instability (HIGH) - Auto-open on repeated failures
- W2: 503 Overload (HIGH) - Count 503/500 errors toward threshold
- W5: Function Calling Signature (HIGH) - Handle InvalidArgument

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Circuit tripped, requests fail fast to fallback
- HALF_OPEN: Recovery probe, one request allowed to test

Configuration:
- failure_threshold: 5 failures within window opens circuit
- recovery_timeout: 60 seconds before recovery attempt
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class CircuitBreakerOpen(Exception):
    """Raised when circuit is open and requests should use fallback."""
    
    def __init__(self, name: str, last_failure_time: float, recovery_in: float):
        self.name = name
        self.last_failure_time = last_failure_time
        self.recovery_in = recovery_in
        super().__init__(
            f"CircuitBreaker '{name}' is OPEN. "
            f"Recovery in {recovery_in:.1f}s"
        )


@dataclass
class FailureRecord:
    """Record of a single failure."""
    timestamp: float
    error_type: str = "unknown"


class CircuitBreaker:
    """
    Thread-safe Circuit Breaker for Gemini API protection.
    
    Usage:
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
        
        try:
            cb.check_state()  # Raises CircuitBreakerOpen if OPEN
            response = await gemini_api_call()
            cb.record_success()
        except CircuitBreakerOpen:
            # Use fallback model
            response = await fallback_model_call()
        except GeminiError as e:
            cb.record_failure(error_type=type(e).__name__)
            raise
    """
    
    # Error types that trigger circuit breaker
    TRIGGERING_ERRORS = {
        "ServiceUnavailable",      # 503
        "InternalServerError",     # 500
        "ResourceExhausted",       # 429
        "DeadlineExceeded",        # Timeout
        "InvalidArgument",         # 400 - includes signature validation
        "SAFETY_FINISH",           # SAFETY finish reason
        "RECITATION_FINISH",       # RECITATION finish reason
        "OTHER_FINISH",            # OTHER finish reason
        "MAX_TOKENS_FINISH",       # MAX_TOKENS finish reason
    }
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "gemini_primary",
        failure_window: float = 60.0
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures to open circuit
            recovery_timeout: Seconds before attempting recovery
            name: Identifier for logging/metrics
            failure_window: Seconds to keep failure records
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        self.failure_window = failure_window
        
        # State
        self._state = "CLOSED"
        self._failures: List[FailureRecord] = []
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0
        
        # Metrics
        self._total_failures = 0
        self._total_successes = 0
        
        # Thread safety
        self._lock = threading.RLock()
    
    @property
    def state(self) -> str:
        """Get current state, checking for automatic transition to HALF_OPEN."""
        with self._lock:
            if self._state == "OPEN":
                # Check if recovery timeout has passed
                if time.time() - self._opened_at >= self.recovery_timeout:
                    self._state = "HALF_OPEN"
                    logger.info(
                        f"CircuitBreaker '{self.name}' transitioned to HALF_OPEN "
                        f"after {self.recovery_timeout}s recovery timeout"
                    )
            return self._state
    
    @property
    def failure_count(self) -> int:
        """Get current failure count within window."""
        with self._lock:
            self._clean_old_failures_internal()
            return len(self._failures)
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open."""
        return self.state == "OPEN"
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed."""
        return self.state == "CLOSED"
    
    def check_state(self) -> None:
        """
        Check if requests are allowed. Raises CircuitBreakerOpen if not.
        
        Call this before making API requests to fail fast.
        """
        with self._lock:
            current_state = self.state  # This may trigger HALF_OPEN transition
            
            if current_state == "OPEN":
                recovery_in = max(
                    0,
                    self.recovery_timeout - (time.time() - self._opened_at)
                )
                raise CircuitBreakerOpen(
                    name=self.name,
                    last_failure_time=self._last_failure_time,
                    recovery_in=recovery_in
                )
    
    def record_failure(self, error_type: str = "unknown") -> None:
        """
        Record a failure. Opens circuit if threshold reached.
        
        Args:
            error_type: Type of error (for metrics/logging)
        """
        with self._lock:
            now = time.time()
            
            # Record the failure
            self._failures.append(FailureRecord(timestamp=now, error_type=error_type))
            self._last_failure_time = now
            self._total_failures += 1
            
            # Clean old failures first
            self._clean_old_failures_internal()
            
            logger.debug(
                f"CircuitBreaker '{self.name}' recorded failure: {error_type}. "
                f"Count: {len(self._failures)}/{self.failure_threshold}"
            )
            
            # Check if we should open the circuit
            if self._state == "HALF_OPEN":
                # Any failure in HALF_OPEN reopens immediately
                self._state = "OPEN"
                self._opened_at = now
                logger.warning(
                    f"CircuitBreaker '{self.name}' REOPENED from HALF_OPEN. "
                    f"Error: {error_type}"
                )
            elif self._state == "CLOSED" and len(self._failures) >= self.failure_threshold:
                # Threshold reached, open circuit
                self._state = "OPEN"
                self._opened_at = now
                logger.warning(
                    f"CircuitBreaker '{self.name}' OPENED after "
                    f"{len(self._failures)} failures. Last error: {error_type}"
                )
    
    def record_success(self) -> None:
        """
        Record a success. Closes circuit if in HALF_OPEN state.
        """
        with self._lock:
            self._total_successes += 1
            
            if self._state == "HALF_OPEN":
                # Success in HALF_OPEN closes the circuit
                self._state = "CLOSED"
                self._failures.clear()
                logger.info(
                    f"CircuitBreaker '{self.name}' CLOSED after successful recovery"
                )
            elif self._state == "CLOSED":
                # Reset failure count on success
                self._failures.clear()
    
    def clean_old_failures(self, window_seconds: Optional[float] = None) -> int:
        """
        Clean failures older than window. Returns count of cleaned failures.
        
        Args:
            window_seconds: Override failure window (default: self.failure_window)
        """
        with self._lock:
            return self._clean_old_failures_internal(window_seconds)
    
    def _clean_old_failures_internal(self, window_seconds: Optional[float] = None) -> int:
        """Internal cleanup without lock (caller must hold lock)."""
        window = window_seconds if window_seconds is not None else self.failure_window
        cutoff = time.time() - window
        
        old_count = len(self._failures)
        self._failures = [f for f in self._failures if f.timestamp >= cutoff]
        cleaned = old_count - len(self._failures)
        
        if cleaned > 0:
            logger.debug(
                f"CircuitBreaker '{self.name}' cleaned {cleaned} old failures"
            )
        
        return cleaned
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get circuit breaker metrics for monitoring."""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state,
                "failure_count": len(self._failures),
                "failure_threshold": self.failure_threshold,
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "last_failure_time": self._last_failure_time,
                "recovery_timeout": self.recovery_timeout,
                "opened_at": self._opened_at if self._state != "CLOSED" else None,
            }
    
    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED state."""
        with self._lock:
            self._state = "CLOSED"
            self._failures.clear()
            self._opened_at = 0.0
            logger.info(f"CircuitBreaker '{self.name}' manually reset to CLOSED")
    
    def force_open(self) -> None:
        """Manually force circuit breaker to OPEN state (for testing/emergency)."""
        with self._lock:
            self._state = "OPEN"
            self._opened_at = time.time()
            logger.warning(f"CircuitBreaker '{self.name}' manually forced OPEN")
    
    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name='{self.name}', state='{self.state}', "
            f"failures={self.failure_count}/{self.failure_threshold})"
        )
