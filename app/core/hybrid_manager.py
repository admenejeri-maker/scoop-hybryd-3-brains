"""
Scoop AI Hybrid Inference Manager
=================================

Orchestrates the hybrid inference architecture:
1. CircuitBreaker - API stability protection
2. TokenCounter - Context window management
3. ModelRouter - Model selection logic
4. FallbackTrigger - Error detection and fallback decisions

This manager provides a unified interface for the ConversationEngine.

Architecture Flow:
```
Request → TokenCounter → ModelRouter → Primary Model
                              ↓
                         CircuitBreaker.is_allowed?
                              ↓
                         SUCCESS → Update state
                         FAILURE → FallbackTrigger.analyze()
                              ↓
                         Retry? → Primary Model (retry)
                         Fallback? → Fallback Model
```
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from .circuit_breaker import CircuitBreaker
from .token_counter import TokenCounter
from .model_router import ModelRouter, RoutingDecision
from .fallback_trigger import FallbackTrigger, FallbackReason, FallbackDecision

logger = logging.getLogger(__name__)


@dataclass
class HybridConfig:
    """Configuration for HybridInferenceManager."""
    # Model configuration
    primary_model: str = "gemini-3-flash-preview"
    fallback_model: str = "gemini-2.5-flash"
    extended_model: str = "gemini-2.5-pro"
    
    # Circuit breaker settings
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: float = 60.0
    
    # Token counter settings
    extended_context_threshold: int = 150000
    safety_multiplier: float = 1.1
    
    # Retry settings
    max_retries: int = 2
    
    @classmethod
    def from_settings(cls) -> "HybridConfig":
        """Create config from application settings."""
        try:
            from config import settings
            return cls(
                primary_model=settings.primary_model,
                fallback_model=settings.fallback_model,
                extended_model=settings.extended_model,
                circuit_failure_threshold=settings.circuit_failure_threshold,
                circuit_recovery_seconds=settings.circuit_recovery_seconds,
                extended_context_threshold=settings.extended_context_threshold,
            )
        except ImportError:
            logger.warning("Settings not available, using defaults")
            return cls()


@dataclass
class InferenceMetrics:
    """Metrics for hybrid inference operations."""
    total_requests: int = 0
    primary_successes: int = 0
    fallback_uses: int = 0
    extended_uses: int = 0
    circuit_trips: int = 0
    retries: int = 0
    safety_blocks: int = 0
    recitation_blocks: int = 0
    
    def to_dict(self) -> Dict[str, int]:
        """Export metrics as dict."""
        return {
            "total_requests": self.total_requests,
            "primary_successes": self.primary_successes,
            "fallback_uses": self.fallback_uses,
            "extended_uses": self.extended_uses,
            "circuit_trips": self.circuit_trips,
            "retries": self.retries,
            "safety_blocks": self.safety_blocks,
            "recitation_blocks": self.recitation_blocks,
        }


class HybridInferenceManager:
    """
    Unified manager for hybrid inference architecture.
    
    Provides a clean interface for the ConversationEngine to:
    1. Select the appropriate model for each request
    2. Handle fallback scenarios automatically
    3. Track metrics across all components
    
    Usage:
        manager = HybridInferenceManager()
        
        # Get routing decision before making API call
        routing = manager.route_request(
            message="რომელი პროტეინი გირჩევ?",
            history=[...],
            force_fallback=False
        )
        
        # After API call, record result
        manager.record_success(routing.model)
        # OR
        should_retry = manager.record_failure(exception, routing)
    """
    
    def __init__(self, config: Optional[HybridConfig] = None):
        """
        Initialize hybrid inference manager.
        
        Args:
            config: Optional configuration (loads from settings if not provided)
        """
        self.config = config or HybridConfig.from_settings()
        
        # Initialize components
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=self.config.circuit_failure_threshold,
            recovery_timeout=self.config.circuit_recovery_seconds,
        )
        
        self.token_counter = TokenCounter(
            extended_threshold=self.config.extended_context_threshold,
            safety_multiplier=self.config.safety_multiplier,
        )
        
        self.model_router = ModelRouter(
            primary_model=self.config.primary_model,
            fallback_model=self.config.fallback_model,
            extended_model=self.config.extended_model,
            extended_threshold=self.config.extended_context_threshold,
            circuit_breaker=self.circuit_breaker,
        )
        
        self.fallback_trigger = FallbackTrigger()
        
        # Internal metrics
        self._metrics = InferenceMetrics()
        self._last_routing: Optional[RoutingDecision] = None
        
        logger.info(
            f"HybridInferenceManager initialized: "
            f"primary={self.config.primary_model}, "
            f"threshold={self.config.extended_context_threshold}"
        )
    
    # =========================================================================
    # MAIN ROUTING INTERFACE
    # =========================================================================
    
    def route_request(
        self,
        message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        force_fallback: bool = False,
    ) -> RoutingDecision:
        """
        Route a request to the appropriate model.
        
        Args:
            message: User message
            history: Conversation history (BSON format)
            force_fallback: Force use of fallback model
            
        Returns:
            RoutingDecision with model name and configuration
        """
        self._metrics.total_requests += 1
        
        # Step 1: Compute token count via TokenCounter
        message_tokens = self.token_counter.estimate_tokens(message)
        history_tokens = self.token_counter.count_history_tokens(history or [])
        token_count = message_tokens + history_tokens
        
        # Step 2: Get routing decision from ModelRouter
        result = self.model_router.route(
            token_count=token_count,
            force_fallback=force_fallback,
        )
        
        # Track which model was selected
        if result.model == self.config.primary_model:
            pass  # Will count on success
        elif result.model == self.config.extended_model:
            self._metrics.extended_uses += 1
        elif result.model == self.config.fallback_model:
            self._metrics.fallback_uses += 1
        
        self._last_routing = result
        
        logger.info(
            f"Routed to {result.model}: reason={result.reason}, "
            f"tokens={result.token_count}"
        )
        
        return result
    
    def record_success(self, model: Optional[str] = None) -> None:
        """
        Record a successful API call.
        
        Args:
            model: Model that succeeded (uses last routing if not provided)
        """
        model = model or (self._last_routing.model if self._last_routing else None)
        
        if model == self.config.primary_model:
            self._metrics.primary_successes += 1
            self.circuit_breaker.record_success()
    
    def record_failure(
        self,
        exception: Optional[Exception] = None,
        response: Optional[Any] = None,
    ) -> Tuple[bool, Optional[RoutingDecision]]:
        """
        Record a failed API call and decide on retry/fallback.
        
        Args:
            exception: Exception that occurred
            response: Response that triggered failure (if any)
            
        Returns:
            Tuple of (should_retry, fallback_routing)
            - If should_retry is True, retry with same model
            - If should_retry is False and fallback_routing is not None, use fallback
            - If both are False/None, give up
        """
        # Analyze the failure
        if exception:
            decision = self.fallback_trigger.analyze_exception(exception)
        elif response:
            decision = self.fallback_trigger.analyze_response(response)
        else:
            decision = FallbackDecision(
                should_fallback=True,
                reason=FallbackReason.UNKNOWN_ERROR,
                details="No exception or response provided",
                retryable=True,
                severity=1,
            )
        
        # Record to circuit breaker
        self.circuit_breaker.record_failure()
        
        # Track specific failures
        if decision.reason == FallbackReason.SAFETY_BLOCK:
            self._metrics.safety_blocks += 1
        elif decision.reason == FallbackReason.RECITATION_BLOCK:
            self._metrics.recitation_blocks += 1
        
        # Check circuit breaker
        if self.circuit_breaker.state == "OPEN":
            self._metrics.circuit_trips += 1
        
        # Decide: retry or fallback?
        if decision.retryable and self._metrics.retries < self.config.max_retries:
            self._metrics.retries += 1
            logger.info(f"Retry {self._metrics.retries}/{self.config.max_retries}")
            return (True, None)
        
        # Fallback
        if decision.should_fallback:
            fallback_routing = self.model_router.route(
                token_count=0,
                force_fallback=True,
            )
            self._metrics.fallback_uses += 1
            logger.warning(
                f"Falling back to {fallback_routing.model}: "
                f"reason={decision.reason.value}"
            )
            return (False, fallback_routing)
        
        return (False, None)

    # =========================================================================
    # FALLBACK MODEL SELECTION
    # =========================================================================

    def get_fallback_model(self, current_model: Optional[str] = None) -> Optional[str]:
        """
        Get the fallback model for a given model.

        Model hierarchy (for SAFETY/stability):
        - primary (gemini-3-flash-preview) → extended (gemini-2.5-pro) [most stable]
        - extended (gemini-2.5-pro) → fallback (gemini-2.5-flash) [last resort]
        - fallback (gemini-2.5-flash) → None (no more fallbacks)

        Note: For SAFETY issues, we prefer gemini-2.5-pro because it's more stable
        and less SAFETY-sensitive than gemini-2.5-flash.

        Args:
            current_model: The model that failed. If None, returns fallback for primary.

        Returns:
            Fallback model name, or None if no fallback available
        """
        if current_model is None:
            current_model = self.config.primary_model

        # Normalize model name for comparison
        current = current_model.lower() if current_model else ""

        # primary → extended (gemini-2.5-pro is most stable for SAFETY)
        if "3-flash" in current or current == self.config.primary_model.lower():
            logger.info(
                f"📥 Fallback for '{current_model}' → '{self.config.extended_model}' (stable)"
            )
            return self.config.extended_model

        # extended → fallback (last resort)
        if "2.5-pro" in current or current == self.config.extended_model.lower():
            logger.info(
                f"📥 Fallback for '{current_model}' → '{self.config.fallback_model}' (last resort)"
            )
            return self.config.fallback_model

        # fallback or unknown → no fallback
        logger.warning(f"📥 No fallback available for '{current_model}'")
        return None

    # =========================================================================
    # STATUS AND METRICS
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current status of all components.
        
        Returns:
            Dict with status of each component
        """
        return {
            "circuit_breaker": {
                "state": self.circuit_breaker.state,
                "failure_count": self.circuit_breaker.failure_count,
                "is_closed": self.circuit_breaker.is_closed,
            },
            "token_counter": {
                "extended_threshold": self.token_counter.extended_threshold,
            },
            "model_router": {
                "primary": self.model_router.primary_model,
                "fallback": self.model_router.fallback_model,
                "extended": self.model_router.extended_model,
            },
            "fallback_trigger": self.fallback_trigger.get_metrics(),
            "manager_metrics": self._metrics.to_dict(),
        }
    
    def get_metrics(self) -> Dict[str, int]:
        """Get manager-level metrics."""
        return self._metrics.to_dict()
    
    def reset_metrics(self) -> None:
        """Reset all metrics to zero."""
        self._metrics = InferenceMetrics()
        self.fallback_trigger.reset_metrics()
    
    @property
    def circuit_state(self) -> str:
        """Get current circuit breaker state."""
        return self.circuit_breaker.state
    
    @property  
    def is_healthy(self) -> bool:
        """Check if primary model is usable."""
        return self.circuit_breaker.state != "OPEN"


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_hybrid_manager() -> HybridInferenceManager:
    """
    Factory to create HybridInferenceManager with app settings.
    
    Returns:
        Configured HybridInferenceManager
    """
    config = HybridConfig.from_settings()
    return HybridInferenceManager(config=config)
