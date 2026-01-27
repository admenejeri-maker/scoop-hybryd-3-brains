"""
TokenCounter - Phase 2 of Hybrid Inference Architecture

Estimates token count for context window management.
Based on 16-point weakness mitigation framework from challenge analysis.

Key Mitigations:
- W3: Context Window Limit (200k for Flash) - Route to extended model at threshold
- Safety Buffer: Use 1.2x multiplier to prevent overflow

Token Estimation Strategy:
- Heuristic: ~4 chars per token for English
- Unicode (Georgian): ~2 chars per token (higher bytes)
- History: Sum all message parts
- Threshold: 150k tokens triggers extended context model (gemini-2.5-pro)
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class TokenEstimate:
    """Result of token estimation with metadata."""
    tokens: int
    chars: int
    avg_chars_per_token: float
    has_unicode: bool


class TokenCounter:
    """
    Heuristic token counter for context window management.
    
    Uses character-based estimation (no API call) for performance.
    Falls back to extended model (gemini-2.5-pro) when threshold exceeded.
    
    Usage:
        counter = TokenCounter(extended_threshold=150_000)
        
        if counter.needs_extended_context(history):
            # Use gemini-2.5-pro (1M context)
        else:
            # Use primary model
    """
    
    # Character per token ratios for different content types
    CHARS_PER_TOKEN_ENGLISH = 4.0
    CHARS_PER_TOKEN_UNICODE = 2.0  # Georgian, Chinese, etc. use more tokens
    
    def __init__(
        self,
        chars_per_token: float = 4.0,
        extended_threshold: int = 150_000,
        safety_multiplier: float = 1.0,
        unicode_multiplier: float = 2.0
    ):
        """
        Initialize token counter.
        
        Args:
            chars_per_token: Base ratio for English text
            extended_threshold: Token count to trigger extended context
            safety_multiplier: Multiply estimates for safety buffer
            unicode_multiplier: Factor for non-ASCII characters
        """
        self.chars_per_token = chars_per_token
        self.extended_threshold = extended_threshold
        self.safety_multiplier = safety_multiplier
        self.unicode_multiplier = unicode_multiplier
    
    def estimate_tokens(
        self,
        text: str,
        with_safety_buffer: bool = False
    ) -> int:
        """
        Estimate token count for a text string.
        
        Args:
            text: Text to estimate tokens for
            with_safety_buffer: Apply safety multiplier
            
        Returns:
            Estimated token count
        """
        if not text:
            return 0
        
        # Count ASCII vs Unicode characters
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        unicode_chars = len(text) - ascii_chars
        
        # Calculate tokens with different rates
        ascii_tokens = ascii_chars / self.chars_per_token
        unicode_tokens = unicode_chars / (self.chars_per_token / self.unicode_multiplier)
        
        total = int(ascii_tokens + unicode_tokens)
        
        # Apply safety buffer if requested
        if with_safety_buffer:
            total = int(total * self.safety_multiplier)
        
        return total
    
    def count_history_tokens(self, history: List[Dict[str, Any]]) -> int:
        """
        Count tokens in conversation history.
        
        Args:
            history: List of message dictionaries with 'role' and 'parts'
            
        Returns:
            Total estimated tokens
        """
        if not history:
            return 0
        
        total = 0
        for message in history:
            parts = message.get("parts", [])
            for part in parts:
                if isinstance(part, dict) and "text" in part:
                    total += self.estimate_tokens(part["text"])
                elif isinstance(part, str):
                    total += self.estimate_tokens(part)
            
            # Add overhead for role and structure (~10 tokens per message)
            total += 10
        
        return total
    
    def needs_extended_context(self, history: List[Dict[str, Any]]) -> bool:
        """
        Check if history exceeds extended context threshold.
        
        Args:
            history: Conversation history
            
        Returns:
            True if extended context model should be used
        """
        count = self.count_history_tokens(history)
        return count >= self.extended_threshold
    
    def get_breakdown(
        self,
        history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Get detailed token breakdown for debugging.
        
        Args:
            history: Conversation history
            
        Returns:
            Dictionary with per-message token counts
        """
        per_message = []
        total = 0
        
        for i, message in enumerate(history):
            parts = message.get("parts", [])
            message_tokens = 10  # Overhead
            
            for part in parts:
                if isinstance(part, dict) and "text" in part:
                    message_tokens += self.estimate_tokens(part["text"])
                elif isinstance(part, str):
                    message_tokens += self.estimate_tokens(part)
            
            per_message.append({
                "index": i,
                "role": message.get("role", "unknown"),
                "tokens": message_tokens
            })
            total += message_tokens
        
        return {
            "total_tokens": total,
            "message_count": len(history),
            "per_message": per_message,
            "extended_threshold": self.extended_threshold,
            "needs_extended": total >= self.extended_threshold
        }
    
    def get_context_info(
        self,
        history: List[Dict[str, Any]],
        system_prompt_tokens: int = 0,
        max_context: int = 200_000
    ) -> Dict[str, Any]:
        """
        Get context utilization information.
        
        Args:
            history: Conversation history
            system_prompt_tokens: Pre-counted system prompt tokens
            max_context: Maximum context window size
            
        Returns:
            Context utilization details
        """
        history_tokens = self.count_history_tokens(history)
        total_tokens = history_tokens + system_prompt_tokens
        utilization_pct = (total_tokens / max_context) * 100
        
        return {
            "history_tokens": history_tokens,
            "system_tokens": system_prompt_tokens,
            "total_tokens": total_tokens,
            "max_context": max_context,
            "utilization_pct": round(utilization_pct, 2),
            "available_tokens": max_context - total_tokens,
            "needs_extended": total_tokens >= self.extended_threshold,
            "extended_threshold": self.extended_threshold
        }
    
    def __repr__(self) -> str:
        return (
            f"TokenCounter(chars_per_token={self.chars_per_token}, "
            f"extended_threshold={self.extended_threshold})"
        )
