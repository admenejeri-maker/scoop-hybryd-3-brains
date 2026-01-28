"""
Fact Extractor Service (Phase 4)
================================

Uses Gemini to extract permanent user attributes from conversation history
before pruning. This is the "brain" that replaces the simple FACT: heuristic.

Design:
- Analyzes pruned messages to find long-term user facts
- Uses Gemini for intelligent extraction
- Returns structured facts ready for UserStore
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


# =============================================================================
# EXTRACTION PROMPT (Georgian-aware)
# =============================================================================

FACT_EXTRACTION_PROMPT = """შეაანალიზე ეს საუბარი და ამოიღე მხოლოდ მუდმივი, გრძელვადიანი ფაქტები მომხმარებლის შესახებ.

**რა უნდა ამოიღო:**
- ალერგიები (მაგ: "თხილზე ალერგიული", "ლაქტოზის აუტანლობა")
- ჯანმრთელობის მდგომარეობა (მაგ: "დიაბეტი", "ორსული")
- ფიზიკური მონაცემები (მაგ: "80კგ წონა", "180სმ სიმაღლე", "35 წლის")
- მიზნები (მაგ: "კუნთის მასის ზრდა", "წონის დაკლება")
- პრეფერენციები (მაგ: "ვეგანი", "ბიუჯეტი 100₾-მდე")
- პირადი ინფორმაცია (მაგ: "სახელი გიორგი", "სქესი")

**რა არ უნდა ამოიღო:**
- ერთჯერადი კითხვები ("რამდენი ღირს პროტეინი?")
- მისალმებები და ზოგადი საუბარი
- პროდუქტის რეკომენდაციები

**ფორმატი:** დააბრუნე მხოლოდ JSON მასივი, სხვა ტექსტი არ დასჭირდება:
[
  {{
    "fact": "ფაქტის ტექსტი ქართულად",
    "importance": 0.1-1.0,
    "category": "health|allergy|preference|goal|physical"
  }}
]

თუ ვერ ამოიღე რაიმე მნიშვნელოვანი, დააბრუნე ცარიელი მასივი: []

**საუბარი:**
{conversation}
"""


class FactExtractor:
    """
    Extracts permanent user facts from conversation using Gemini.
    
    Usage:
        extractor = FactExtractor(api_key="...")
        facts = await extractor.extract_facts(messages)
        # Returns: [{"fact": "...", "importance": 0.8, "category": "health"}]
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize FactExtractor.
        
        Args:
            api_key: Gemini API key. If None, fetched from settings.
        """
        if api_key is None:
            from config import settings
            api_key = settings.gemini_api_key
        
        self.client = genai.Client(api_key=api_key)
        # Use Flash for cost-efficiency (extraction doesn't need Pro)
        self.model_name = "gemini-2.0-flash"
        
        logger.info(f"FactExtractor initialized with model={self.model_name}")
    
    async def extract_facts(
        self,
        messages: List[Dict[str, Any]],
        max_chars: int = 6000,
        max_retries: int = 3,
        base_delay: float = 1.0
    ) -> List[Dict[str, Any]]:
        """
        Extract facts from conversation messages with retry logic.
        
        Args:
            messages: List of message dicts with 'role' and 'parts'
            max_chars: Maximum characters to analyze (truncates old messages)
            max_retries: Maximum retry attempts for transient errors
            base_delay: Base delay in seconds for exponential backoff
        
        Returns:
            List of extracted facts with 'fact', 'importance', 'category'
        """
        if not messages:
            return []
        
        # Convert messages to readable text
        conversation_text = self._messages_to_text(messages, max_chars)
        
        if len(conversation_text) < 50:
            logger.debug("Conversation too short for fact extraction")
            return []
        
        # Build prompt
        prompt = FACT_EXTRACTION_PROMPT.format(conversation=conversation_text)
        
        # Retry loop with exponential backoff
        last_error = None
        for attempt in range(max_retries):
            try:
                # Call Gemini (async via thread)
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,  # Low temp for structured output
                        max_output_tokens=1024,
                    )
                )
                
                # Parse JSON response
                facts = self._parse_response(response)
                
                logger.info(f"Extracted {len(facts)} facts from {len(messages)} messages")
                return facts
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                # Check for retryable errors (rate limit, server errors)
                is_retryable = any(code in error_str for code in [
                    "429", "503", "500", "resourceexhausted", 
                    "rate limit", "overloaded", "timeout"
                ])
                
                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(
                        f"Retryable error in fact extraction (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    break
        
        logger.error(f"Fact extraction failed after {max_retries} attempts: {last_error}")
        return []
    
    def _messages_to_text(
        self,
        messages: List[Dict[str, Any]],
        max_chars: int
    ) -> str:
        """
        Convert messages to readable conversation text.
        Supports both BSON dicts and SDK Content objects.
        
        Args:
            messages: Message dicts or SDK Content objects
            max_chars: Max output length
        
        Returns:
            Formatted conversation string
        """
        lines = []
        
        for msg in messages:
            # Support both BSON dict and SDK Content objects
            if hasattr(msg, 'role'):  # SDK object
                role = msg.role
                parts = getattr(msg, 'parts', []) or []
            else:  # Dict
                role = msg.get("role", "user")
                parts = msg.get("parts", [])
            
            role_label = "მომხმარებელი" if role == "user" else "ასისტენტი"
            
            for part in parts:
                # Support both dict parts and SDK Part objects
                if hasattr(part, 'text'):  # SDK Part
                    text = part.text or ""
                else:  # Dict
                    text = part.get("text", "")
                
                if text:
                    lines.append(f"{role_label}: {text}")
        
        full_text = "\n".join(lines)
        
        # Truncate if too long (keep end, as recent messages are more relevant)
        if len(full_text) > max_chars:
            full_text = "..." + full_text[-max_chars:]
        
        return full_text
    
    def _parse_response(self, response: Any) -> List[Dict[str, Any]]:
        """
        Parse Gemini response to extract JSON facts.
        
        Args:
            response: Gemini API response
        
        Returns:
            List of fact dicts
        """
        try:
            text = response.text.strip()
            
            # Strategy 1: Handle markdown code blocks
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                text = text[start:end].strip()
            elif "```" in text:
                start = text.find("```") + 3
                end = text.find("```", start)
                text = text[start:end].strip()
            
            # Strategy 2: Try direct JSON parse
            try:
                facts = json.loads(text)
            except json.JSONDecodeError:
                # Strategy 3: Extract JSON array using regex
                # Find first '[' and last ']' to isolate JSON array
                match = re.search(r'\[.*\]', text, re.DOTALL)
                if match:
                    json_text = match.group(0)
                    # Clean trailing commas before closing brackets
                    json_text = re.sub(r',\s*([\]\}])', r'\1', json_text)
                    facts = json.loads(json_text)
                else:
                    logger.warning("No JSON array found in response")
                    return []
            
            if not isinstance(facts, list):
                return []
            
            # Validate structure
            validated = []
            for fact in facts:
                if isinstance(fact, dict) and "fact" in fact:
                    validated.append({
                        "fact": str(fact.get("fact", ""))[:200],
                        "importance": float(fact.get("importance", 0.6)),
                        "category": fact.get("category", "preference"),
                    })
            
            return validated
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse extraction response as JSON: {e}")
            return []
        except Exception as e:
            logger.warning(f"Error parsing extraction response: {e}")
            return []


# =============================================================================
# FACTORY
# =============================================================================

def create_fact_extractor() -> FactExtractor:
    """Create FactExtractor with default settings."""
    return FactExtractor()
