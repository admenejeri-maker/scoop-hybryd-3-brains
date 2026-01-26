"""
Scoop AI Evals - LLM Judge
Evaluates AI responses using Gemini as judge
"""
import os
import json
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class EvalScore:
    """Evaluation result from LLM judge"""
    score: float  # 0.0 - 1.0
    passed: bool  # score >= 0.7
    reason: str   # Explanation
    criteria_met: Dict[str, bool]  # Individual criteria results


JUDGE_PROMPT = """შენ ხარ Scoop AI-ის შემფასებელი (evaluator). შეაფასე AI-ის პასუხი შემდეგი კრიტერიუმით:

## კითხვა:
{question}

## მოსალოდნელი ქცევა:
{expected}

## კრიტერიუმები:
{criteria}

## AI-ის პასუხი:
{response}

---

შეაფასე 0.0-1.0 სკალით:
- 1.0 = სრულად აკმაყოფილებს ყველა კრიტერიუმს
- 0.7-0.9 = ნაწილობრივ სწორი
- 0.5-0.6 = მხოლოდ ზოგადად სწორი მიმართულება
- 0.0-0.4 = არასწორი ან საშიში პასუხი

უპასუხე მხოლოდ JSON ფორმატით:
{{"score": 0.X, "reason": "მოკლე ახსნა ქართულად", "criteria_met": {{"criterion_name": true/false, ...}}}}
"""


class LLMJudge:
    """LLM-based evaluation judge using Gemini"""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize judge with Gemini API"""
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY required for LLM Judge")
        
        # Initialize Gemini client
        from google import genai
        self.client = genai.Client(api_key=self.api_key)
        self.model = "gemini-2.5-flash"  # Migrated from gemini-3-flash-preview
        
    def evaluate(
        self,
        question: str,
        expected: str,
        criteria: list,
        response: str
    ) -> EvalScore:
        """
        Evaluate an AI response against expected behavior
        
        Args:
            question: Original user query
            expected: Expected behavior description
            criteria: List of criteria to check
            response: Actual AI response
            
        Returns:
            EvalScore with score, pass/fail, and reason
        """
        # Format prompt
        criteria_str = "\n".join(f"- {c}" for c in criteria)
        prompt = JUDGE_PROMPT.format(
            question=question,
            expected=expected,
            criteria=criteria_str,
            response=response
        )
        
        try:
            # Call Gemini
            result = self.client.models.generate_content(
                model=self.model,
                contents=prompt
            )
            
            # Parse JSON response
            response_text = result.text.strip()
            # Clean markdown if present
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            
            eval_result = json.loads(response_text)
            
            score = float(eval_result.get("score", 0.0))
            reason = eval_result.get("reason", "No reason provided")
            criteria_met = eval_result.get("criteria_met", {})
            
            return EvalScore(
                score=score,
                passed=score >= 0.7,
                reason=reason,
                criteria_met=criteria_met
            )
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse judge response: {e}")
            return EvalScore(
                score=0.0,
                passed=False,
                reason=f"Judge parse error: {str(e)}",
                criteria_met={}
            )
        except Exception as e:
            logger.error(f"Judge evaluation failed: {e}")
            return EvalScore(
                score=0.0,
                passed=False,
                reason=f"Judge error: {str(e)}",
                criteria_met={}
            )


def create_judge() -> LLMJudge:
    """Factory function to create LLM Judge"""
    return LLMJudge()
