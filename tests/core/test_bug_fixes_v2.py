"""
Bug Fix Tests v2.0
==================

Tests for post-launch bug fixes:
1. Quick Replies truncation recovery (BUG 1)
2. EmptyResponseError forceful stop directive (BUG 2)

Date: 2026-01-22
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.response_buffer import ResponseBuffer
from app.core.tool_executor import ToolExecutor, ToolResult
from app.core.types import FunctionCall


# =============================================================================
# BUG 1: Quick Replies Truncation Tests
# =============================================================================

class TestQuickRepliesTruncation:
    """Test cases for Quick Replies tag truncation recovery."""

    def test_normal_closed_tag_extraction(self):
        """Test Case 1: Normal closed tag - should extract properly."""
        buffer = ResponseBuffer()
        buffer.set_text(
            "Hello [QUICK_REPLIES]- Option 1\n- Option 2[/QUICK_REPLIES] Goodbye"
        )
        
        replies = buffer.parse_quick_replies()
        
        assert len(replies) == 2
        assert replies[0]["title"] == "Option 1"
        assert replies[1]["title"] == "Option 2"
        # Tag should be removed from text
        assert "[QUICK_REPLIES]" not in buffer.get_text()
        assert "Goodbye" in buffer.get_text()

    def test_truncated_unclosed_tag_extraction(self):
        """Test Case 2: Truncated (no closing tag) - should use fallback."""
        buffer = ResponseBuffer()
        buffer.set_text(
            "Hello [QUICK_REPLIES]- პროტეინის დოზირება\n- სხვა ვიტამინები\n- შეკვ..."
        )
        
        replies = buffer.parse_quick_replies()
        
        # Should extract at least 2 items from truncated content
        assert len(replies) >= 2
        assert "პროტეინის დოზირება" in replies[0]["title"]
        assert "სხვა ვიტამინები" in replies[1]["title"]
        # Tag should be removed from text
        assert "[QUICK_REPLIES]" not in buffer.get_text()

    def test_no_tag_returns_empty(self):
        """Test Case 3: No tag at all - should return empty list."""
        buffer = ResponseBuffer()
        buffer.set_text("Just regular text without quick replies")
        
        replies = buffer.parse_quick_replies()
        
        assert len(replies) == 0
        assert buffer.get_text() == "Just regular text without quick replies"

    def test_truncated_mid_word_extraction(self):
        """Test truncated content cut mid-word still extracts valid items."""
        buffer = ResponseBuffer()
        buffer.set_text(
            "რეკომენდაცია [QUICK_REPLIES]\n- კრეატინის მიღება\n- ვარჯიშის გრაფ"
        )
        
        replies = buffer.parse_quick_replies()
        
        # Should extract at least the complete first item
        assert len(replies) >= 1
        assert "კრეატინის მიღება" in replies[0]["title"]

    def test_georgian_fallback_pattern(self):
        """Test Georgian 'შემდეგი ნაბიჯი' fallback pattern."""
        buffer = ResponseBuffer()
        buffer.set_text(
            "რჩევა დასრულდა. შემდეგი ნაბიჯი: პროტეინის შეძენა, ვარჯიშის დაწყება"
        )
        
        replies = buffer.parse_quick_replies()
        
        # Text should be cleaned after extraction
        assert "რჩევა დასრულდა" in buffer.get_text()


# =============================================================================
# BUG 2: EmptyResponseError (Query Limit Forceful Directive) Tests
# =============================================================================

class TestQueryLimitForcefulDirective:
    """Test cases for forceful stop directive on query limit."""

    @pytest.mark.asyncio
    async def test_query_limit_returns_instruction(self):
        """Test Case 2: Query limit response contains forceful instruction."""
        # Create executor with max_unique_queries=1
        mock_search = AsyncMock(return_value={
            "products": [{"id": "1", "name": "Protein"}],
            "count": 1,
        })
        
        executor = ToolExecutor(
            user_id="test_user",
            search_fn=mock_search,
            max_unique_queries=1,
        )
        
        # First search - should execute normally
        result1 = await executor.execute(
            FunctionCall(name="search_products", args={"query": "protein"})
        )
        assert result1.skipped is False
        assert len(result1.products) == 1
        
        # Second search - should hit query limit
        result2 = await executor.execute(
            FunctionCall(name="search_products", args={"query": "creatine"})
        )
        
        # Verify forceful directive
        assert result2.skipped is True
        assert result2.skip_reason == "query_limit"
        assert "instruction" in result2.response
        assert "status" in result2.response
        assert result2.response["status"] == "SEARCH_COMPLETE"
        
        # Verify Georgian directive content
        instruction = result2.response["instruction"]
        assert "აღარ გამოიძახო search_products" in instruction
        assert "საძიებო ლიმიტი ამოიწურა" in instruction
        assert "დაწერე რეკომენდაცია" in instruction

    @pytest.mark.asyncio
    async def test_duplicate_query_returns_cached(self):
        """Test duplicate query returns cached results without forceful directive."""
        mock_search = AsyncMock(return_value={
            "products": [{"id": "1", "name": "Protein"}],
            "count": 1,
        })
        
        executor = ToolExecutor(
            user_id="test_user",
            search_fn=mock_search,
            max_unique_queries=5,  # High limit
        )
        
        # First search
        await executor.execute(
            FunctionCall(name="search_products", args={"query": "protein"})
        )
        
        # Duplicate search - same query
        result2 = await executor.execute(
            FunctionCall(name="search_products", args={"query": "PROTEIN"})  # Case insensitive
        )
        
        assert result2.skipped is True
        assert result2.skip_reason == "duplicate_query"
        # Duplicate should have note, not instruction
        assert "note" in result2.response
        assert "instruction" not in result2.response

    @pytest.mark.asyncio
    async def test_product_count_in_instruction(self):
        """Test instruction includes correct product count."""
        mock_search = AsyncMock(return_value={
            "products": [
                {"id": "1", "name": "Protein 1"},
                {"id": "2", "name": "Protein 2"},
                {"id": "3", "name": "Protein 3"},
            ],
            "count": 3,
        })
        
        executor = ToolExecutor(
            user_id="test_user",
            search_fn=mock_search,
            max_unique_queries=1,
        )
        
        # Execute first query
        await executor.execute(
            FunctionCall(name="search_products", args={"query": "protein"})
        )
        
        # Hit limit
        result = await executor.execute(
            FunctionCall(name="search_products", args={"query": "other"})
        )
        
        # Verify product count in instruction
        assert "ნაპოვნია 3 პროდუქტი" in result.response["instruction"]
        assert result.response["count"] == 3


# =============================================================================
# Integration: Response Buffer + Tool Executor Flow
# =============================================================================

class TestIntegrationFlow:
    """Integration tests for combined buffer and executor behavior."""

    def test_buffer_handles_products_and_truncated_qr(self):
        """Test buffer handles both products and truncated quick replies."""
        buffer = ResponseBuffer()
        
        # Add products
        buffer.add_products([
            {"id": "1", "name": "Whey Protein", "price": 89},
            {"id": "2", "name": "Creatine", "price": 45},
        ])
        
        # Set text with truncated quick replies
        buffer.set_text(
            "აქ არის თქვენი რეკომენდაციები.\n\n"
            "[QUICK_REPLIES]\n"
            "- მეტი ინფორმაცია\n"
            "- შეძენა"  # No closing tag - truncated
        )
        
        # Extract quick replies
        replies = buffer.parse_quick_replies()
        
        assert buffer.get_product_count() == 2
        assert len(replies) >= 2
        assert buffer.has_content() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
