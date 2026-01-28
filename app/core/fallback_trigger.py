"""
Scoop AI Fallback Trigger Detector
==================================

Detects conditions that warrant automatic fallback from primary model.

Expanded Triggers (v3.0):
1. SAFETY blocks (Google content policy)
2. RECITATION blocks (grounding policy)
3. 503 Service Unavailable
4. 500 Internal Server Error
5. 429 Rate Limit (with circuit breaker)
6. Empty responses after retry

Design: Stateless detector - circuit breaker state is managed separately.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FallbackReason(Enum):
    """Categorized fallback reasons for metrics."""
    NONE = "none"
    SAFETY_BLOCK = "safety_block"
    RECITATION_BLOCK = "recitation_block"
    SERVICE_UNAVAILABLE = "503_service_unavailable"
    INTERNAL_ERROR = "500_internal_error"
    RATE_LIMITED = "429_rate_limited"
    EMPTY_RESPONSE = "empty_response"
    INCOMPLETE_RESPONSE = "incomplete_response"  # Text ends mid-sentence (e.g., ":")
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class FallbackDecision:
    """Result of fallback trigger analysis."""
    should_fallback: bool
    reason: FallbackReason
    details: str
    retryable: bool  # Can retry with same model first?
    severity: int    # 1=low, 2=medium, 3=high (for metrics)


# Error patterns for detection
SAFETY_PATTERNS = [
    r"SAFETY",
    r"blocked.*safety",
    r"content.*policy",
    r"HARM_CATEGORY",
    r"safety.*block",
]

RECITATION_PATTERNS = [
    r"RECITATION",
    r"grounding.*policy",
    r"grounding.*block",
    r"source.*attribution",
]

SERVICE_ERROR_PATTERNS = [
    (r"503", FallbackReason.SERVICE_UNAVAILABLE),
    (r"ServiceUnavailable", FallbackReason.SERVICE_UNAVAILABLE),
    (r"500", FallbackReason.INTERNAL_ERROR),
    (r"InternalError", FallbackReason.INTERNAL_ERROR),
    (r"429", FallbackReason.RATE_LIMITED),
    (r"ResourceExhausted", FallbackReason.RATE_LIMITED),
    (r"RESOURCE_EXHAUSTED", FallbackReason.RATE_LIMITED),
]


class FallbackTrigger:
    """
    Detects when to trigger fallback from primary model.
    
    Thread-safe stateless detector. Circuit breaker state management
    is handled separately by the CircuitBreaker class.
    
    Usage:
        trigger = FallbackTrigger()
        
        # Check response candidate
        decision = trigger.analyze_response(response)
        if decision.should_fallback:
            # Route to fallback model
            
        # Check exception
        decision = trigger.analyze_exception(exception)
        if decision.should_fallback and not decision.retryable:
            # Immediate fallback without retry
    """
    
    def __init__(self):
        """Initialize trigger detector."""
        self._safety_regex = re.compile(
            "|".join(SAFETY_PATTERNS), 
            re.IGNORECASE
        )
        self._recitation_regex = re.compile(
            "|".join(RECITATION_PATTERNS), 
            re.IGNORECASE
        )
        self._metrics = {
            "total_analyzed": 0,
            "safety_blocks": 0,
            "recitation_blocks": 0,
            "service_errors": 0,
            "rate_limits": 0,
            "empty_responses": 0,
            "incomplete_responses": 0,
        }
    
    def analyze_response(
        self, 
        response: Any,
        check_empty: bool = True,
    ) -> FallbackDecision:
        """
        Analyze a Gemini response for fallback triggers.
        
        Args:
            response: SDK response object or GenerateContentResponse
            check_empty: Whether to detect empty responses
            
        Returns:
            FallbackDecision with recommendation
        """
        self._metrics["total_analyzed"] += 1
        
        # 1. Check for safety block in candidates
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            
            # Check finish reason
            finish_reason = getattr(candidate, 'finish_reason', None)
            if finish_reason:
                reason_str = str(finish_reason)
                
                # Safety block
                if "SAFETY" in reason_str.upper():
                    self._metrics["safety_blocks"] += 1
                    return FallbackDecision(
                        should_fallback=True,
                        reason=FallbackReason.SAFETY_BLOCK,
                        details=f"Safety block: {reason_str}",
                        retryable=False,  # Don't retry safety blocks
                        severity=3,
                    )
                
                # Recitation block
                if "RECITATION" in reason_str.upper():
                    self._metrics["recitation_blocks"] += 1
                    return FallbackDecision(
                        should_fallback=True,
                        reason=FallbackReason.RECITATION_BLOCK,
                        details=f"Recitation block: {reason_str}",
                        retryable=False,
                        severity=2,
                    )
        
        # 2. Check prompt feedback
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
            feedback = response.prompt_feedback
            block_reason = getattr(feedback, 'block_reason', None)
            if block_reason and block_reason != "BLOCK_REASON_UNSPECIFIED":
                reason_str = str(block_reason)
                if "SAFETY" in reason_str.upper():
                    self._metrics["safety_blocks"] += 1
                    return FallbackDecision(
                        should_fallback=True,
                        reason=FallbackReason.SAFETY_BLOCK,
                        details=f"Prompt blocked: {reason_str}",
                        retryable=False,
                        severity=3,
                    )
        
        # 3. Check for empty response
        if check_empty:
            has_content = self._has_meaningful_content(response)
            if not has_content:
                self._metrics["empty_responses"] += 1
                return FallbackDecision(
                    should_fallback=True,
                    reason=FallbackReason.EMPTY_RESPONSE,
                    details="Response has no meaningful content",
                    retryable=True,  # Can retry once
                    severity=1,
                )
        
        # No fallback needed
        return FallbackDecision(
            should_fallback=False,
            reason=FallbackReason.NONE,
            details="Response OK",
            retryable=False,
            severity=0,
        )
    
    def analyze_exception(self, exception: Exception) -> FallbackDecision:
        """
        Analyze an exception for fallback triggers.
        
        Args:
            exception: Caught exception from API call
            
        Returns:
            FallbackDecision with recommendation
        """
        self._metrics["total_analyzed"] += 1
        error_str = str(exception)
        error_type = type(exception).__name__
        
        # Check error type and message for service errors
        for pattern, reason in SERVICE_ERROR_PATTERNS:
            if re.search(pattern, error_str, re.IGNORECASE) or \
               re.search(pattern, error_type, re.IGNORECASE):
                
                if reason == FallbackReason.RATE_LIMITED:
                    self._metrics["rate_limits"] += 1
                    return FallbackDecision(
                        should_fallback=True,
                        reason=reason,
                        details=f"Rate limited: {error_str[:100]}",
                        retryable=True,  # Retry with backoff
                        severity=2,
                    )
                else:
                    self._metrics["service_errors"] += 1
                    return FallbackDecision(
                        should_fallback=True,
                        reason=reason,
                        details=f"Service error: {error_str[:100]}",
                        retryable=True,  # Retry first
                        severity=2,
                    )
        
        # Check for safety pattern in error message
        if self._safety_regex.search(error_str):
            self._metrics["safety_blocks"] += 1
            return FallbackDecision(
                should_fallback=True,
                reason=FallbackReason.SAFETY_BLOCK,
                details=f"Safety in exception: {error_str[:100]}",
                retryable=False,
                severity=3,
            )
        
        # Check for recitation pattern
        if self._recitation_regex.search(error_str):
            self._metrics["recitation_blocks"] += 1
            return FallbackDecision(
                should_fallback=True,
                reason=FallbackReason.RECITATION_BLOCK,
                details=f"Recitation in exception: {error_str[:100]}",
                retryable=False,
                severity=2,
            )
        
        # Check for timeout
        if "timeout" in error_str.lower() or "TimeoutError" in error_type:
            return FallbackDecision(
                should_fallback=True,
                reason=FallbackReason.TIMEOUT,
                details=f"Request timeout: {error_str[:100]}",
                retryable=True,
                severity=2,
            )
        
        # Unknown error - fallback as precaution
        logger.warning(f"Unknown error triggering fallback: {error_type}: {error_str[:100]}")
        return FallbackDecision(
            should_fallback=True,
            reason=FallbackReason.UNKNOWN_ERROR,
            details=f"Unknown: {error_type}: {error_str[:100]}",
            retryable=True,
            severity=1,
        )
    
    def _has_meaningful_content(self, response: Any) -> bool:
        """
        Check if response has meaningful text or function calls.
        
        Args:
            response: SDK response object
            
        Returns:
            True if response has content
        """
        # Direct text access
        if hasattr(response, 'text') and response.text:
            return len(response.text.strip()) > 0
        
        # Check candidates
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and candidate.content:
                content = candidate.content
                if hasattr(content, 'parts') and content.parts:
                    for part in content.parts:
                        # Text content (excluding pure thoughts)
                        if hasattr(part, 'text') and part.text:
                            if not (hasattr(part, 'thought') and part.thought):
                                if part.text.strip():
                                    return True
                        # Function calls count as content
                        if hasattr(part, 'function_call') and part.function_call:
                            return True
        return False
    
    def get_metrics(self) -> Dict[str, int]:
        """
        Get detection metrics.
        
        Returns:
            Dict with counts by category
        """
        return self._metrics.copy()
    
    def reset_metrics(self) -> None:
        """Reset all metrics to zero."""
        for key in self._metrics:
            self._metrics[key] = 0
    
    def analyze_text_completeness(self, text: str) -> FallbackDecision:
        """
        Check if text response appears incomplete (mid-sentence).
        
        Detects patterns like:
        - Text ending with ":" (incomplete list)
        - Text ending with Georgian conjunctions "და ", "მაგრამ "
        - Very short responses that look cut off
        
        Args:
            text: The accumulated response text
            
        Returns:
            FallbackDecision recommending retry if incomplete
        """
        if not text:
            return FallbackDecision(
                should_fallback=False,
                reason=FallbackReason.NONE,
                details="Empty text - not checking completeness",
                retryable=False,
                severity=0,
            )
        
        stripped = text.strip()
        
        # Must have meaningful content (>50 chars) to be considered incomplete
        # Very short text might be intentional
        if len(stripped) < 50:
            return FallbackDecision(
                should_fallback=False,
                reason=FallbackReason.NONE,
                details="Text too short to check completeness",
                retryable=False,
                severity=0,
            )
        
        # Check for incomplete patterns
        incomplete_patterns = [
            (r':\s*$', "ends with colon (incomplete list)"),
            (r'ვარიანტებია:\s*$', "ends with 'options are:' (incomplete list)"),
            (r'შემდეგია:\s*$', "ends with 'following:' (incomplete list)"),
            (r'და\s*$', "ends with Georgian 'and' conjunction"),
            (r'მაგრამ\s*$', "ends with Georgian 'but' conjunction"),
        ]
        
        for pattern, description in incomplete_patterns:
            if re.search(pattern, stripped):
                self._metrics["incomplete_responses"] += 1
                logger.warning(f"Incomplete response detected: {description}")
                return FallbackDecision(
                    should_fallback=True,
                    reason=FallbackReason.INCOMPLETE_RESPONSE,
                    details=f"Response {description}",
                    retryable=True,  # Can retry with fallback model
                    severity=2,
                )
        
        # Text appears complete
        return FallbackDecision(
            should_fallback=False,
            reason=FallbackReason.NONE,
            details="Response appears complete",
            retryable=False,
            severity=0,
        )
