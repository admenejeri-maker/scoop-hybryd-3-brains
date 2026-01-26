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
    # Question #5: Rate Limits for Gemini 2.5 Flash:
    # - Free tier: 15 RPM, 1M TPM, 1500 RPD
    # - Paid tier: 2000 RPM, 4M TPM (standard), scales with billing
    model_name: str = "gemini-2.5-flash"  # Migrated from gemini-3-flash-preview for stable safety settings

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

    # Gemini 2.5 Thinking Configuration (uses thinking_budget, not thinking_level)
    # HIGH: 16384 tokens = deep reasoning for complex queries
    thinking_budget: int = Field(
        default_factory=lambda: int(os.getenv("THINKING_BUDGET", "16384"))  # HIGH = Deep reasoning
    )
    include_thoughts: bool = Field(
        default_factory=lambda: os.getenv("INCLUDE_THOUGHTS", "true").lower() == "true"  # Re-enabled
    )
    # Gemini 3 thinking depth: MINIMAL, LOW, MEDIUM, HIGH
    # MEDIUM = balanced speed/quality (testing)
    thinking_level: str = Field(
        default_factory=lambda: os.getenv("THINKING_LEVEL", "MEDIUM")
    )

    # Temperature for generation (Gemini 3 recommended: 1.0)
    # NOTE: Google recommends NOT changing from 1.0 for Gemini 3 models
    # as lower values can cause unexpected behavior in math/reasoning tasks
    temperature: float = Field(
        default_factory=lambda: float(os.getenv("TEMPERATURE", "1.0"))
    )

    # Vector Search Configuration
    # Embedding model for semantic product search (768-dim, matches description_embedding)
    embedding_model: str = Field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-004")
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
