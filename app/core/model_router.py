"""
ModelRouter - Phase 3 of Hybrid Inference Architecture

Routes requests to the appropriate model based on:
- Circuit breaker state (OPEN → fallback)
- Token count (>150k → extended context)
- Model capabilities (thinking configuration)

Based on 16-point weakness mitigation framework:
- W1: Preview Instability → Circuit breaker routing
- W3: Context Window → Extended model for large contexts
- W4: ThinkingLevel vs ThinkingBudget → Model-specific config
"""
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import logging

from app.core.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a specific model."""
    name: str
    supports_thinking: bool = False
    thinking_param: Optional[str] = None  # "thinking_level" or "thinking_budget"
    thinking_value: Optional[Any] = None  # "HIGH" or 16384
    max_context: int = 200_000
    max_output: int = 8192


@dataclass
class RoutingDecision:
    """Result of routing decision."""
    model: str
    reason: str
    config: Optional[ModelConfig] = None
    token_count: int = 0
    
    @property
    def is_primary(self) -> bool:
        return self.reason in ("primary_healthy", "primary_selected")


class ModelRouter:
    """
    Routes API requests to appropriate Gemini models.
    
    Routing Priority (highest to lowest):
    1. Force fallback flag (override)
    2. Circuit breaker OPEN → fallback
    3. Token count > threshold → extended
    4. Default → primary
    
    Usage:
        router = ModelRouter(
            primary_model="gemini-3-flash-preview",
            fallback_model="gemini-2.5-flash",
            extended_model="gemini-2.5-pro"
        )
        
        decision = router.route(token_count=180_000)
        # -> RoutingDecision(model="gemini-2.5-pro", reason="extended_context")
    """
    
    # Model configurations with thinking parameter differences
    MODEL_CONFIGS = {
        # Gemini 3.x uses thinkingLevel: LOW, MEDIUM, HIGH
        "gemini-3-flash-preview": ModelConfig(
            name="gemini-3-flash-preview",
            supports_thinking=True,
            thinking_param="thinking_level",
            thinking_value="HIGH",
            max_context=200_000,
            max_output=8192
        ),
        "gemini-3-flash": ModelConfig(
            name="gemini-3-flash",
            supports_thinking=True,
            thinking_param="thinking_level",
            thinking_value="HIGH",
            max_context=200_000,
            max_output=8192
        ),
        # Gemini 2.5 uses thinkingBudget: 0-24576 or -1 (dynamic)
        "gemini-2.5-pro": ModelConfig(
            name="gemini-2.5-pro",
            supports_thinking=True,
            thinking_param="thinking_budget",
            thinking_value=16384,
            max_context=1_000_000,  # 1M context
            max_output=8192
        ),
        "gemini-2.5-flash": ModelConfig(
            name="gemini-2.5-flash",
            supports_thinking=True,
            thinking_param="thinking_budget",
            thinking_value=8192,
            max_context=1_000_000,
            max_output=8192
        ),
    }
    
    def __init__(
        self,
        primary_model: str = "gemini-3-flash-preview",
        fallback_model: str = "gemini-2.5-flash",
        extended_model: str = "gemini-2.5-pro",
        extended_threshold: int = 150_000,
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize model router.
        
        Args:
            primary_model: Default model for requests
            fallback_model: Model when circuit is open
            extended_model: Model for large contexts
            extended_threshold: Token count to trigger extended
            circuit_breaker: Optional circuit breaker instance
        """
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.extended_model = extended_model
        self.extended_threshold = extended_threshold
        
        # Use provided circuit breaker or create new one
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
            name="gemini_primary"
        )
        
        # Metrics
        self._total_routes = 0
        self._primary_routes = 0
        self._fallback_routes = 0
        self._extended_routes = 0
        
        # Thread safety
        self._lock = threading.RLock()
    
    def route(
        self,
        token_count: int = 0,
        force_fallback: bool = False
    ) -> RoutingDecision:
        """
        Determine which model to use for a request.
        
        Args:
            token_count: Estimated token count for request
            force_fallback: Force use of fallback model
            
        Returns:
            RoutingDecision with model and reason
        """
        with self._lock:
            self._total_routes += 1
            
            # Priority 1: Force fallback
            if force_fallback:
                self._fallback_routes += 1
                return RoutingDecision(
                    model=self.fallback_model,
                    reason="forced_fallback",
                    config=self.get_model_config(self.fallback_model),
                    token_count=token_count
                )
            
            # Priority 2: Circuit breaker open
            if self.circuit_breaker.is_open:
                self._fallback_routes += 1
                logger.warning(
                    f"Circuit open, routing to fallback: {self.fallback_model}"
                )
                return RoutingDecision(
                    model=self.fallback_model,
                    reason="circuit_open",
                    config=self.get_model_config(self.fallback_model),
                    token_count=token_count
                )
            
            # Priority 3: Extended context needed
            if token_count >= self.extended_threshold:
                self._extended_routes += 1
                logger.info(
                    f"Token count {token_count} >= {self.extended_threshold}, "
                    f"routing to extended: {self.extended_model}"
                )
                return RoutingDecision(
                    model=self.extended_model,
                    reason="extended_context",
                    config=self.get_model_config(self.extended_model),
                    token_count=token_count
                )
            
            # Default: Primary model
            self._primary_routes += 1
            return RoutingDecision(
                model=self.primary_model,
                reason="primary_healthy",
                config=self.get_model_config(self.primary_model),
                token_count=token_count
            )
    
    def get_model_config(self, model_name: str) -> ModelConfig:
        """
        Get configuration for a model.
        
        Args:
            model_name: Model identifier
            
        Returns:
            ModelConfig with thinking parameters
        """
        # Look for exact match first
        if model_name in self.MODEL_CONFIGS:
            return self.MODEL_CONFIGS[model_name]
        
        # Check for partial match (handle version suffixes)
        for key, config in self.MODEL_CONFIGS.items():
            if model_name.startswith(key) or key.startswith(model_name):
                return config
        
        # Unknown model - return safe defaults
        logger.warning(f"Unknown model {model_name}, using safe defaults")
        return ModelConfig(
            name=model_name,
            supports_thinking=False,
            thinking_param=None,
            thinking_value=None,
            max_context=200_000,
            max_output=8192
        )
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get routing metrics."""
        with self._lock:
            return {
                "total_routes": self._total_routes,
                "primary_routes": self._primary_routes,
                "fallback_routes": self._fallback_routes,
                "extended_routes": self._extended_routes,
                "circuit_state": self.circuit_breaker.state,
                "circuit_failures": self.circuit_breaker.failure_count,
            }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive routing summary."""
        with self._lock:
            return {
                "primary_model": self.primary_model,
                "fallback_model": self.fallback_model,
                "extended_model": self.extended_model,
                "extended_threshold": self.extended_threshold,
                "circuit_state": self.circuit_breaker.state,
                "metrics": self.get_metrics(),
            }
    
    def __repr__(self) -> str:
        return (
            f"ModelRouter(primary={self.primary_model}, "
            f"fallback={self.fallback_model}, "
            f"extended={self.extended_model})"
        )
