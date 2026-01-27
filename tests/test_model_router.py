"""
Test suite for ModelRouter - Phase 3 of Hybrid Inference Architecture

TDD Step 1: Write failing tests first

ModelRouter routes requests to the appropriate model based on:
- Circuit breaker state (OPEN → fallback)
- Token count (>150k → extended context)
- Model capabilities (thinking configuration)
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Import will fail initially (TDD - expected)
from app.core.model_router import ModelRouter, RoutingDecision, ModelConfig
from app.core.circuit_breaker import CircuitBreaker


class TestModelRouterBasics:
    """Basic routing functionality tests."""

    @pytest.fixture
    def router(self):
        """Create a model router with test configuration."""
        return ModelRouter(
            primary_model="gemini-3-flash-preview",
            fallback_model="gemini-2.5-flash", 
            extended_model="gemini-2.5-pro",
            extended_threshold=150_000,
            circuit_breaker=CircuitBreaker(failure_threshold=3)
        )

    def test_normal_routing_uses_primary(self, router):
        """Normal conditions should use primary model."""
        decision = router.route(token_count=10_000)
        
        assert decision.model == "gemini-3-flash-preview"
        assert decision.reason == "primary_healthy"
        assert decision.is_primary

    def test_extended_context_routing(self, router):
        """High token count should use extended model."""
        decision = router.route(token_count=180_000)
        
        assert decision.model == "gemini-2.5-pro"
        assert decision.reason == "extended_context"
        assert not decision.is_primary

    def test_circuit_open_uses_fallback(self, router):
        """Open circuit should use fallback model."""
        # Open the circuit
        for _ in range(3):
            router.circuit_breaker.record_failure()
        
        decision = router.route(token_count=10_000)
        
        assert decision.model == "gemini-2.5-flash"
        assert decision.reason == "circuit_open"
        assert not decision.is_primary

    def test_force_fallback_flag(self, router):
        """Force fallback flag should override normal routing."""
        decision = router.route(token_count=10_000, force_fallback=True)
        
        assert decision.model == "gemini-2.5-flash"
        assert decision.reason == "forced_fallback"


class TestModelConfiguration:
    """Test model-specific configuration."""

    @pytest.fixture
    def router(self):
        """Create a router for config testing."""
        return ModelRouter(
            primary_model="gemini-3-flash-preview",
            fallback_model="gemini-2.5-flash",
            extended_model="gemini-2.5-pro",
            extended_threshold=150_000
        )

    def test_gemini3_uses_thinking_level(self, router):
        """Gemini 3.x models should use thinkingLevel config."""
        config = router.get_model_config("gemini-3-flash-preview")
        
        assert config.thinking_param == "thinking_level"
        assert config.thinking_value == "HIGH"
        assert config.supports_thinking

    def test_gemini25_uses_thinking_budget(self, router):
        """Gemini 2.5 models should use thinkingBudget config."""
        config = router.get_model_config("gemini-2.5-pro")
        
        assert config.thinking_param == "thinking_budget"
        assert config.thinking_value == 16384
        assert config.supports_thinking

    def test_unknown_model_defaults(self, router):
        """Unknown models should use safe defaults."""
        config = router.get_model_config("unknown-model")
        
        assert config.supports_thinking is False
        assert config.thinking_param is None


class TestRoutingPriority:
    """Test routing priority order."""

    @pytest.fixture
    def router(self):
        """Create a router for priority testing."""
        return ModelRouter(
            primary_model="gemini-3-flash-preview",
            fallback_model="gemini-2.5-flash",
            extended_model="gemini-2.5-pro",
            extended_threshold=150_000,
            circuit_breaker=CircuitBreaker(failure_threshold=2)
        )

    def test_circuit_open_beats_extended(self, router):
        """Circuit open should have priority over extended context."""
        # Open the circuit
        for _ in range(2):
            router.circuit_breaker.record_failure()
        
        # Even with high token count, should use fallback (not extended)
        decision = router.route(token_count=180_000)
        
        assert decision.model == "gemini-2.5-flash"
        assert decision.reason == "circuit_open"

    def test_force_fallback_beats_all(self, router):
        """Force fallback should override everything."""
        decision = router.route(
            token_count=180_000,
            force_fallback=True
        )
        
        assert decision.model == "gemini-2.5-flash"
        assert decision.reason == "forced_fallback"


class TestRoutingMetrics:
    """Test routing metrics and observability."""

    @pytest.fixture
    def router(self):
        """Create a router for metrics testing."""
        return ModelRouter(
            primary_model="gemini-3-flash-preview",
            fallback_model="gemini-2.5-flash",
            extended_model="gemini-2.5-pro",
            extended_threshold=150_000
        )

    def test_routing_tracks_counts(self, router):
        """Should track routing decision counts."""
        router.route(token_count=10_000)
        router.route(token_count=10_000)
        router.route(token_count=180_000)
        
        metrics = router.get_metrics()
        
        assert metrics["total_routes"] == 3
        assert metrics["primary_routes"] == 2
        assert metrics["extended_routes"] == 1

    def test_get_routing_summary(self, router):
        """Should return comprehensive routing summary."""
        router.route(token_count=10_000)
        
        summary = router.get_summary()
        
        assert "primary_model" in summary
        assert "fallback_model" in summary
        assert "extended_model" in summary
        assert "circuit_state" in summary


class TestConcurrentRouting:
    """Test thread-safety under concurrent routing."""

    @pytest.fixture
    def router(self):
        """Create a router for concurrency testing."""
        return ModelRouter(
            primary_model="gemini-3-flash-preview",
            fallback_model="gemini-2.5-flash",
            extended_model="gemini-2.5-pro",
            extended_threshold=150_000
        )

    @pytest.mark.asyncio
    async def test_concurrent_routing_safe(self, router):
        """Concurrent routing should be thread-safe."""
        async def route_request():
            return router.route(token_count=10_000)

        # Run 100 concurrent routing decisions
        tasks = [route_request() for _ in range(100)]
        results = await asyncio.gather(*tasks)

        # All should succeed
        assert len(results) == 100
        assert all(r.model == "gemini-3-flash-preview" for r in results)

    @pytest.mark.asyncio
    async def test_mixed_concurrent_routing(self, router):
        """Mixed routing types should be handled correctly."""
        async def route_normal():
            return router.route(token_count=10_000)

        async def route_extended():
            return router.route(token_count=180_000)

        tasks = [route_normal() for _ in range(50)]
        tasks.extend([route_extended() for _ in range(50)])
        results = await asyncio.gather(*tasks)

        primary_count = sum(1 for r in results if r.is_primary)
        extended_count = sum(1 for r in results if r.model == "gemini-2.5-pro")

        assert primary_count == 50
        assert extended_count == 50
