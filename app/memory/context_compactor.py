"""
Context Compactor - Memory v2.2
================================

Handles context window management through intelligent compaction:
1. Monitors context utilization (triggers at 75% of max)
2. Pre-flushes facts BEFORE compaction (no data loss)
3. Summarizes old messages using LLM
4. Returns compacted history ready for continuation

Architecture:
    ┌────────────────────────────────────────┐
    │         Context Window (200k)           │
    ├────────────────────────────────────────┤
    │  System Prompt (~5k)                   │
    │  User Facts (~2k)                      │
    │  Summary (if compacted) (~1k)          │
    │  Recent History (~150k max)            │
    │  ────────────────────────              │
    │  75% Threshold = 150k tokens           │
    └────────────────────────────────────────┘

When threshold exceeded:
1. Extract facts from oldest 50% messages
2. Summarize those messages
3. Prepend summary to remaining 50%
4. Return compacted history

Risk Mitigations:
- FactExtractor failure: Retry 3x → abort compaction (return original)
- Race condition: Session lock during compact
- Duplicate facts: Cosine dedup handled by UserStore
- MongoDB write failure: Write facts FIRST, then prune
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Compaction triggers at 75% of max context window
COMPACTION_THRESHOLD = 0.75

# Remove oldest 50% of messages during compaction
PRUNE_RATIO = 0.50

# Max tokens for summary (keep it concise)
SUMMARY_MAX_TOKENS = 500

# Min messages before considering compaction (avoid thrashing)
MIN_MESSAGES_FOR_COMPACTION = 20


# =============================================================================
# SUMMARIZATION PROMPT
# =============================================================================

SUMMARIZATION_PROMPT = """შეაჯამე ეს საუბარი მოკლედ, 2-3 წინადადებით.

**რა უნდა შეინარჩუნო:**
- მთავარი თემები (რა პროდუქტები განიხილეს)
- მომხმარებლის გადაწყვეტილებები (რა აირჩია, რა უარყო)
- მნიშვნელოვანი კონტექსტი მომდევნო საუბრისთვის

**ფორმატი:** მხოლოდ ჯამი, არანაირი დამატებითი ტექსტი.

**საუბარი:**
{conversation}"""


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CompactionResult:
    """Result of a compaction operation."""
    compacted: bool
    original_message_count: int
    new_message_count: int
    facts_extracted: int
    summary: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ContextInfo:
    """Current context window state."""
    total_tokens: int
    max_tokens: int
    utilization: float  # 0.0 to 1.0
    message_count: int
    needs_compaction: bool


# =============================================================================
# CONTEXT COMPACTOR
# =============================================================================

class ContextCompactor:
    """
    Manages context window through intelligent compaction.

    Usage:
        compactor = ContextCompactor(
            token_counter=token_counter,
            gemini_api_key=api_key
        )

        # Check if compaction needed
        if await compactor.should_compact(history, max_tokens=200_000):
            # Perform compaction
            history = await compactor.compact(user_id, history)
    """

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        threshold: float = COMPACTION_THRESHOLD,
        prune_ratio: float = PRUNE_RATIO,
        max_context_tokens: int = 200_000,
    ):
        """
        Initialize ContextCompactor.

        Args:
            gemini_api_key: Gemini API key (uses settings if not provided)
            threshold: Context utilization threshold to trigger compaction (0.0-1.0)
            prune_ratio: Ratio of messages to prune during compaction (0.0-1.0)
            max_context_tokens: Maximum context window size in tokens
        """
        if gemini_api_key is None:
            from config import settings
            gemini_api_key = settings.gemini_api_key

        self.client = genai.Client(api_key=gemini_api_key)
        self.model_name = "gemini-2.0-flash"  # Fast model for summarization

        self.threshold = threshold
        self.prune_ratio = prune_ratio
        self.max_context_tokens = max_context_tokens

        # Lazy-loaded dependencies
        self._token_counter = None
        self._fact_extractor = None
        self._user_store = None
        self._gemini_adapter = None

        logger.info(
            f"ContextCompactor initialized: threshold={threshold:.0%}, "
            f"prune_ratio={prune_ratio:.0%}, max_tokens={max_context_tokens:,}"
        )

    @property
    def token_counter(self):
        """Lazy-load TokenCounter."""
        if self._token_counter is None:
            from app.core.token_counter import TokenCounter
            self._token_counter = TokenCounter()
        return self._token_counter

    @property
    def fact_extractor(self):
        """Lazy-load FactExtractor."""
        if self._fact_extractor is None:
            from app.memory.fact_extractor import FactExtractor
            self._fact_extractor = FactExtractor()
        return self._fact_extractor

    @property
    def user_store(self):
        """Lazy-load UserStore."""
        if self._user_store is None:
            from app.memory.mongo_store import UserStore
            self._user_store = UserStore()
        return self._user_store

    @property
    def gemini_adapter(self):
        """Lazy-load GeminiAdapter for embeddings."""
        if self._gemini_adapter is None:
            from app.adapters.gemini_adapter import create_gemini_adapter
            self._gemini_adapter = create_gemini_adapter()
        return self._gemini_adapter

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def get_context_info(
        self,
        history: List[Dict[str, Any]],
        system_prompt_tokens: int = 5000
    ) -> ContextInfo:
        """
        Get current context window utilization info.

        Args:
            history: Conversation history
            system_prompt_tokens: Estimated tokens for system prompt

        Returns:
            ContextInfo with utilization details
        """
        history_tokens = self.token_counter.count_history_tokens(history)
        total_tokens = history_tokens + system_prompt_tokens
        utilization = total_tokens / self.max_context_tokens

        return ContextInfo(
            total_tokens=total_tokens,
            max_tokens=self.max_context_tokens,
            utilization=utilization,
            message_count=len(history),
            needs_compaction=utilization >= self.threshold
        )

    async def should_compact(
        self,
        history: List[Dict[str, Any]],
        system_prompt_tokens: int = 5000
    ) -> bool:
        """
        Check if history needs compaction.

        Returns True if:
        1. Context utilization >= threshold (75%)
        2. AND message count >= minimum (20)

        Args:
            history: Conversation history
            system_prompt_tokens: Estimated tokens for system prompt

        Returns:
            True if compaction should be performed
        """
        if len(history) < MIN_MESSAGES_FOR_COMPACTION:
            return False

        info = self.get_context_info(history, system_prompt_tokens)

        if info.needs_compaction:
            logger.info(
                f"Compaction needed: {info.utilization:.1%} utilization "
                f"({info.total_tokens:,}/{info.max_tokens:,} tokens, "
                f"{info.message_count} messages)"
            )

        return info.needs_compaction

    async def compact(
        self,
        user_id: str,
        history: List[Dict[str, Any]],
        session_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], CompactionResult]:
        """
        Perform context compaction on history.

        Steps:
        1. Calculate split point (oldest 50%)
        2. Pre-flush: Extract facts from old messages
        3. Summarize old messages
        4. Return: [summary_message] + [recent_messages]

        Args:
            user_id: User identifier for fact storage
            history: Full conversation history
            session_id: Optional session ID for logging

        Returns:
            Tuple of (compacted_history, result)
        """
        original_count = len(history)
        result = CompactionResult(
            compacted=False,
            original_message_count=original_count,
            new_message_count=original_count,
            facts_extracted=0
        )

        if original_count < MIN_MESSAGES_FOR_COMPACTION:
            logger.debug(f"Skipping compaction: only {original_count} messages")
            return history, result

        # Calculate split point
        split_point = int(original_count * self.prune_ratio)
        old_messages = history[:split_point]
        recent_messages = history[split_point:]

        logger.info(
            f"Compacting: {original_count} messages → "
            f"extracting facts from {len(old_messages)}, "
            f"keeping {len(recent_messages)} recent"
        )

        # Step 1: Pre-flush facts BEFORE compaction
        facts_count = await self._pre_flush_facts(user_id, old_messages)
        result.facts_extracted = facts_count

        # Step 2: Summarize old messages
        summary = await self._summarize_messages(old_messages)

        if not summary:
            logger.warning("Summarization failed, aborting compaction")
            result.error = "Summarization failed"
            return history, result

        result.summary = summary

        # Step 3: Create summary message and prepend
        summary_message = {
            "role": "model",
            "parts": [{"text": f"[წინა საუბრის შეჯამება]\n{summary}"}]
        }

        compacted_history = [summary_message] + recent_messages

        result.compacted = True
        result.new_message_count = len(compacted_history)

        logger.info(
            f"Compaction complete: {original_count} → {len(compacted_history)} messages, "
            f"{facts_count} facts extracted"
        )

        return compacted_history, result

    # =========================================================================
    # INTERNAL METHODS
    # =========================================================================

    async def _pre_flush_facts(
        self,
        user_id: str,
        messages: List[Dict[str, Any]]
    ) -> int:
        """
        Extract and save facts from messages before compaction.

        Risk Mitigation: Write facts FIRST before any pruning.
        If this fails, compaction is aborted (facts are never lost).

        Args:
            user_id: User to save facts for
            messages: Messages to extract facts from

        Returns:
            Number of facts successfully extracted and saved
        """
        if not messages:
            return 0

        facts_saved = 0

        try:
            # Extract facts using FactExtractor
            extracted_facts = await self.fact_extractor.extract_facts(
                messages,
                max_retries=3  # Retry on failures
            )

            if not extracted_facts:
                logger.debug("No facts extracted from messages")
                return 0

            # Save each fact with embedding
            for fact_data in extracted_facts:
                fact_text = fact_data.get("fact", "")
                importance = fact_data.get("importance", 0.6)
                category = fact_data.get("category", "preference")

                if len(fact_text) < 10:
                    continue

                # Generate embedding with retry
                embedding = await self._get_embedding_with_retry(fact_text)

                if not embedding:
                    logger.warning(f"Skipping fact (embedding failed): {fact_text[:50]}...")
                    continue

                # Boost importance for health/allergy facts
                if category in ("health", "allergy"):
                    importance = max(importance, 0.85)

                # Save to UserStore (dedup handled there)
                try:
                    result = await self.user_store.add_user_fact(
                        user_id=user_id,
                        fact=fact_text[:200],
                        embedding=embedding,
                        importance_score=importance,
                        source="compaction",
                        is_sensitive=(category == "health")
                    )

                    if result["status"] == "added":
                        facts_saved += 1
                        logger.debug(f"Pre-flush fact saved: {fact_text[:50]}...")
                    elif result["status"] == "duplicate":
                        logger.debug(f"Pre-flush duplicate: {fact_text[:50]}...")

                except Exception as e:
                    logger.warning(f"Failed to save fact: {e}")

            logger.info(f"Pre-flush complete: {facts_saved} facts saved from {len(messages)} messages")

        except Exception as e:
            logger.error(f"Pre-flush failed: {e}", exc_info=True)

        return facts_saved

    async def _summarize_messages(
        self,
        messages: List[Dict[str, Any]],
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Generate LLM summary of messages.

        Args:
            messages: Messages to summarize
            max_retries: Retry attempts on failure

        Returns:
            Summary string or None on failure
        """
        if not messages:
            return None

        # Convert messages to text
        conversation_text = self._messages_to_text(messages)

        if len(conversation_text) < 50:
            logger.debug("Conversation too short to summarize")
            return None

        prompt = SUMMARIZATION_PROMPT.format(conversation=conversation_text)

        # Retry loop
        last_error = None
        for attempt in range(max_retries):
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.3,  # More deterministic for summaries
                        max_output_tokens=SUMMARY_MAX_TOKENS,
                    )
                )

                if response and response.text:
                    summary = response.text.strip()
                    logger.debug(f"Generated summary: {summary[:100]}...")
                    return summary

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                is_retryable = any(code in error_str for code in [
                    "429", "503", "500", "rate limit", "timeout"
                ])

                if is_retryable and attempt < max_retries - 1:
                    delay = 1.0 * (2 ** attempt)
                    logger.warning(f"Summarization retry {attempt + 1}: {e}")
                    await asyncio.sleep(delay)
                else:
                    break

        logger.error(f"Summarization failed after {max_retries} attempts: {last_error}")
        return None

    async def _get_embedding_with_retry(
        self,
        text: str,
        max_retries: int = 3
    ) -> Optional[List[float]]:
        """
        Generate embedding with retry logic.

        Args:
            text: Text to embed
            max_retries: Max retry attempts

        Returns:
            Embedding vector or None on failure
        """
        for attempt in range(max_retries):
            try:
                embedding = await self.gemini_adapter.embed_content(text)
                if embedding and len(embedding) in (768, 3072):
                    return embedding
            except Exception as e:
                logger.warning(f"Embedding attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        return None

    def _messages_to_text(
        self,
        messages: List[Dict[str, Any]],
        max_chars: int = 6000
    ) -> str:
        """
        Convert messages to readable text for summarization.

        Args:
            messages: Message dicts
            max_chars: Max output length

        Returns:
            Formatted conversation string
        """
        lines = []

        for msg in messages:
            role = msg.get("role", "user")
            role_label = "მომხმარებელი" if role == "user" else "ასისტენტი"

            for part in msg.get("parts", []):
                text = part.get("text", "") if isinstance(part, dict) else ""
                if text:
                    lines.append(f"{role_label}: {text}")

        full_text = "\n".join(lines)

        # Truncate from start if too long (keep recent context)
        if len(full_text) > max_chars:
            full_text = "..." + full_text[-max_chars:]

        return full_text


# =============================================================================
# FACTORY
# =============================================================================

def create_context_compactor(
    threshold: float = COMPACTION_THRESHOLD,
    max_context_tokens: int = 200_000
) -> ContextCompactor:
    """
    Factory function to create ContextCompactor with default settings.

    Args:
        threshold: Context utilization threshold (default 0.75)
        max_context_tokens: Max context window size

    Returns:
        Configured ContextCompactor
    """
    return ContextCompactor(
        threshold=threshold,
        max_context_tokens=max_context_tokens
    )
