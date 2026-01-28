"""
Configuration for Scoop GenAI - Google Gemini SDK Implementation
Answers Question #5: Production Considerations & #6: Security
"""
import os
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseModel):
    """Application settings with production defaults"""

    # ==========================================================================
    # ENGINE VERSION (v2.0 is now the ONLY path)
    # ==========================================================================
    # v2.0 unified ConversationEngine is now the default and only implementation
    # Legacy v1.0 chat_stream has been removed
    engine_version: str = "v2"

    # Google Gemini API
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))

    # MongoDB
    mongodb_uri: str = Field(default_factory=lambda: os.getenv("MONGODB_URI", ""))
    mongodb_database: str = Field(default_factory=lambda: os.getenv("MONGODB_DATABASE", "scoop_db"))

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = Field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")

    # Model Configuration
    # Gemini 2.5 Pro: Stable GA reasoning model
    # - Price: $1.25/1M input, $10.00/1M output
    # - Thinking: uses thinking_budget (0-24576, -1 for dynamic)
    model_name: str = "gemini-2.5-pro"  # Stable GA model with strong reasoning

    # Session & Memory
    # Question #1: Memory Persistence - Session TTL
    session_ttl_seconds: int = 3600  # 1 hour (longer than Claude version)

    # Question #1: Token Limit Management
    # Gemini 2.5 Flash context: 1M tokens input, but recommend limiting for cost
    max_history_messages: int = 100  # Sliding window trigger
    max_history_tokens: int = 50000  # When to summarize

    # Catalog
    # Question #3: 315 products ~60k tokens
    catalog_cache_ttl_seconds: int = 3600  # 1 hour cache

    # Rate Limiting
    rate_limit_per_minute: int = 30

    # CORS - Use env var for production restriction, default "*" for dev
    allowed_origins: str = Field(default_factory=lambda: os.getenv("ALLOWED_ORIGINS", "*"))

    # Question #6: Security - Content filtering
    enable_safety_settings: bool = True

    # Security: Admin token for protected endpoints
    admin_token: Optional[str] = Field(default_factory=lambda: os.getenv("ADMIN_TOKEN"))

    # Gemini 3 Compatibility Settings
    gemini_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))
    )
    max_output_tokens: int = Field(
        default_factory=lambda: int(os.getenv("MAX_OUTPUT_TOKENS", "8192"))
    )
    # FIX: Maximum function calls for automatic function calling
    # Increased to 5 to prevent EmptyResponseError on complex queries
    # Each extra call adds ~3-5s latency but prevents empty response crashes
    max_function_calls: int = Field(
        default_factory=lambda: int(os.getenv("MAX_FUNCTION_CALLS", "5"))
    )

    # Gemini 2.5 Pro Thinking Configuration
    # Uses thinking_budget (0-24576), NOT thinking_level
    # Set to -1 for dynamic thinking (auto-adjust based on complexity)
    thinking_budget: int = Field(
        default_factory=lambda: int(os.getenv("THINKING_BUDGET", "16384"))  # HIGH = Deep reasoning
    )
    include_thoughts: bool = Field(
        default_factory=lambda: os.getenv("INCLUDE_THOUGHTS", "true").lower() == "true"
    )
    # Legacy: thinking_level for Gemini 3 compatibility
    thinking_level: str = Field(
        default_factory=lambda: os.getenv("THINKING_LEVEL", "HIGH")
    )

    # Temperature for generation (Gemini 3 recommended: 1.0)
    # NOTE: Google recommends NOT changing from 1.0 for Gemini 3 models
    # as lower values can cause unexpected behavior in math/reasoning tasks
    temperature: float = Field(
        default_factory=lambda: float(os.getenv("TEMPERATURE", "1.0"))
    )

    # ==========================================================================
    # HYBRID INFERENCE ARCHITECTURE (v3.0)
    # ==========================================================================
    # Primary: gemini-3-flash-preview (reasoning, thinkingLevel)
    # Extended: gemini-2.5-pro (1M context, thinkingBudget)
    # Fallback: gemini-2.5-flash (reliability, thinkingBudget)
    primary_model: str = Field(
        default_factory=lambda: os.getenv("PRIMARY_MODEL", "gemini-3-flash-preview")
    )
    fallback_model: str = Field(
        default_factory=lambda: os.getenv("FALLBACK_MODEL", "gemini-2.5-flash")
    )
    extended_model: str = Field(
        default_factory=lambda: os.getenv("EXTENDED_MODEL", "gemini-2.5-pro")
    )

    # Circuit Breaker: Open after N failures within recovery window
    circuit_failure_threshold: int = Field(
        default_factory=lambda: int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
    )
    circuit_recovery_seconds: float = Field(
        default_factory=lambda: float(os.getenv("CIRCUIT_RECOVERY_SECONDS", "60.0"))
    )

    # Extended Context: Route to extended model when history exceeds threshold
    # Default 150k (75% of gemini-3-flash-preview's 200k limit)
    extended_context_threshold: int = Field(
        default_factory=lambda: int(os.getenv("EXTENDED_CONTEXT_THRESHOLD", "150000"))
    )

    # Vector Search Configuration
    # Embedding model for semantic search (3072-dim with new SDK)
    # NOTE: New google.genai SDK uses gemini-embedding-001 (text-embedding-004 deprecated)
    embedding_model: str = Field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
    )

    # Week 4: Context Caching Settings
    # ENABLED: System prompt (~5k) + Catalog (~60k) = ~65k tokens > 32k minimum ✅
    # Saves 30-50% TTFT by caching system context between requests
    enable_context_caching: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_CONTEXT_CACHING", "true").lower() == "true"
    )
    # Cache TTL in minutes (1-60, default 60)
    context_cache_ttl_minutes: int = Field(
        default_factory=lambda: int(os.getenv("CONTEXT_CACHE_TTL_MINUTES", "60"))
    )
    # Minutes before expiry to refresh cache (default 10)
    cache_refresh_before_expiry_minutes: int = Field(
        default_factory=lambda: int(os.getenv("CACHE_REFRESH_BEFORE_EXPIRY_MINUTES", "10"))
    )
    # Interval in minutes to check cache health (default 5)
    cache_check_interval_minutes: int = Field(
        default_factory=lambda: int(os.getenv("CACHE_CHECK_INTERVAL_MINUTES", "5"))
    )

    class Config:
        env_file = ".env"


# System Prompt - Choose between full and lean versions
# Lean version is ~50% smaller for faster Gemini response times
import os
USE_LEAN_PROMPT = os.getenv("USE_LEAN_PROMPT", "true").lower() == "true"

if USE_LEAN_PROMPT:
    from prompts.system_prompt_lean import SYSTEM_PROMPT_LEAN as SYSTEM_PROMPT
else:
    from prompts import SYSTEM_PROMPT


settings = Settings()
