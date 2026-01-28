"""
Unit tests for Search-First Architecture intent classification.

Tests the _is_product_query() method to ensure:
1. Product queries are correctly identified
2. Negative filters (past tense, complaints) work
3. Non-product queries are not misclassified
"""

import sys
import os

# Add backend root to path for app imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from unittest.mock import MagicMock, patch


class TestIsProductQuery:
    """Test the intent classifier for Search-First architecture."""

    def setup_method(self):
        """Set up test engine instance."""
        # We need to import inside the method to avoid import issues
        from app.core.engine import ConversationEngine

        # Create mock adapters
        mock_gemini = MagicMock()
        mock_mongo = MagicMock()

        self.engine = ConversationEngine(
            gemini_adapter=mock_gemini,
            mongo_adapter=mock_mongo,
        )

    # =========================================================================
    # POSITIVE TESTS - Should detect product intent
    # =========================================================================

    def test_product_query_with_intent_verb(self):
        """'მინდა პროტეინი' should trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "მინდა პროტეინი", history_len=0
        )
        assert should_search is True
        assert keyword == "პროტეინ"

    def test_product_query_with_question(self):
        """'რა პროტეინი გაქვთ?' should trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "რა პროტეინი გაქვთ?", history_len=0
        )
        assert should_search is True
        assert keyword == "პროტეინ"

    def test_product_query_creatine(self):
        """'მჭირდება კრეატინი' should trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "მჭირდება კრეატინი", history_len=0
        )
        assert should_search is True
        assert keyword == "კრეატინ"

    def test_product_query_best_protein(self):
        """'საუკეთესო პროტეინი?' should trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "საუკეთესო პროტეინი?", history_len=0
        )
        assert should_search is True
        assert keyword == "პროტეინ"

    # =========================================================================
    # NEGATIVE TESTS - Should NOT trigger search (past tense, complaints)
    # =========================================================================

    def test_past_tense_bought(self):
        """'ვიყიდე პროტეინი' (I bought) should NOT trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "ვიყიდე პროტეინი", history_len=0
        )
        assert should_search is False
        assert keyword is None

    def test_past_tense_tried(self):
        """'ვცადე კრეატინი' (I tried) should NOT trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "ვცადე კრეატინი", history_len=0
        )
        assert should_search is False
        assert keyword is None

    def test_complaint_bad(self):
        """'ცუდი პროტეინი იყო' (was bad) should NOT trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "ცუდი პროტეინი იყო", history_len=0
        )
        assert should_search is False
        assert keyword is None

    def test_complaint_return(self):
        """'დაბრუნება მინდა' (want return) should NOT trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "დაბრუნება მინდა პროტეინის", history_len=0
        )
        assert should_search is False
        assert keyword is None

    # =========================================================================
    # NON-PRODUCT TESTS - No product keywords
    # =========================================================================

    def test_greeting(self):
        """'გამარჯობა' should NOT trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "გამარჯობა", history_len=0
        )
        assert should_search is False
        assert keyword is None

    def test_age_statement(self):
        """'50 წლის ვარ' should NOT trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "50 წლის ვარ", history_len=0
        )
        assert should_search is False
        assert keyword is None

    def test_general_question(self):
        """'როგორ ხარ?' should NOT trigger search."""
        should_search, keyword = self.engine._is_product_query(
            "როგორ ხარ?", history_len=0
        )
        assert should_search is False
        assert keyword is None

    # =========================================================================
    # HISTORY LENGTH TESTS - Skip mid-conversation
    # =========================================================================

    def test_skip_mid_conversation(self):
        """Product query should be skipped if history_len > 4."""
        should_search, keyword = self.engine._is_product_query(
            "მინდა პროტეინი", history_len=6
        )
        assert should_search is False
        assert keyword is None

    def test_allow_early_conversation(self):
        """Product query should work if history_len <= 4."""
        should_search, keyword = self.engine._is_product_query(
            "მინდა პროტეინი", history_len=2
        )
        assert should_search is True
        assert keyword == "პროტეინ"

    # =========================================================================
    # EDGE CASES
    # =========================================================================

    def test_keyword_without_intent(self):
        """'პროტეინი' alone (no verb, no question) should NOT trigger."""
        should_search, keyword = self.engine._is_product_query(
            "პროტეინი", history_len=0
        )
        # Has keyword but no intent signal
        assert should_search is False

    def test_english_keyword(self):
        """'I want protein' with English keyword should work."""
        should_search, keyword = self.engine._is_product_query(
            "მინდა protein", history_len=0
        )
        assert should_search is True
        assert keyword == "protein"


class TestFormatProductsForInjection:
    """Test product formatting for context injection."""

    def setup_method(self):
        """Set up test engine instance."""
        from app.core.engine import ConversationEngine

        mock_gemini = MagicMock()
        mock_mongo = MagicMock()

        self.engine = ConversationEngine(
            gemini_adapter=mock_gemini,
            mongo_adapter=mock_mongo,
        )

    def test_format_single_product(self):
        """Single product should format correctly."""
        products = [
            {"name": "Whey Protein", "price": 89, "brand": "ON"}
        ]
        result = self.engine._format_products_for_injection(products)
        assert "1. Whey Protein - 89₾ (ON)" in result

    def test_format_no_brand(self):
        """Product without brand should format without parentheses."""
        products = [
            {"name": "Creatine", "price": 45}
        ]
        result = self.engine._format_products_for_injection(products)
        assert "1. Creatine - 45₾" in result
        assert "()" not in result

    def test_format_limit_to_5(self):
        """Should limit to 5 products max."""
        products = [
            {"name": f"Product {i}", "price": i * 10}
            for i in range(10)
        ]
        result = self.engine._format_products_for_injection(products)
        lines = result.strip().split("\n")
        assert len(lines) == 5

    def test_format_empty_list(self):
        """Empty product list should return empty string."""
        result = self.engine._format_products_for_injection([])
        assert result == ""
