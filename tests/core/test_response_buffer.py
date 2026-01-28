"""
Tests for ResponseBuffer (v2.0)
===============================

Comprehensive tests for the thread-safe response accumulator.

Tests cover:
1. Text operations (append, set, get)
2. Product deduplication
3. TIP extraction (single extraction point)
4. Quick replies parsing
5. Thread safety
6. Snapshot immutability
"""

import sys
import os

# Add backend root to path for app imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
import threading
import time
from typing import Dict, Any, List

from app.core.response_buffer import ResponseBuffer, BufferState


# =============================================================================
# TEXT OPERATIONS
# =============================================================================

class TestTextOperations:
    """Tests for text append/set/get operations."""

    def test_append_text_basic(self):
        """Test basic text appending."""
        buffer = ResponseBuffer()
        buffer.append_text("Hello ")
        buffer.append_text("world!")

        assert buffer.get_text() == "Hello world!"

    def test_append_text_empty(self):
        """Test appending empty string does nothing."""
        buffer = ResponseBuffer()
        buffer.append_text("Hello")
        buffer.append_text("")
        buffer.append_text(None)  # type: ignore

        assert buffer.get_text() == "Hello"

    def test_set_text_replaces(self):
        """Test set_text replaces existing content."""
        buffer = ResponseBuffer()
        buffer.append_text("First")
        buffer.set_text("Second")

        assert buffer.get_text() == "Second"

    def test_has_text_with_content(self):
        """Test has_text returns True when content exists."""
        buffer = ResponseBuffer()
        buffer.append_text("Content")

        assert buffer.has_text() is True

    def test_has_text_with_whitespace_only(self):
        """Test has_text returns False for whitespace only."""
        buffer = ResponseBuffer()
        buffer.append_text("   \n\t  ")

        assert buffer.has_text() is False

    def test_has_text_empty(self):
        """Test has_text returns False when empty."""
        buffer = ResponseBuffer()

        assert buffer.has_text() is False


# =============================================================================
# PRODUCT DEDUPLICATION
# =============================================================================

class TestProductDeduplication:
    """Tests for product deduplication logic."""

    def test_add_products_basic(self):
        """Test adding products."""
        buffer = ResponseBuffer()
        products = [
            {"id": "1", "name": "Protein"},
            {"id": "2", "name": "Creatine"},
        ]

        added = buffer.add_products(products)

        assert added == 2
        assert buffer.get_product_count() == 2

    def test_add_products_deduplication_by_id(self):
        """Test products are deduplicated by 'id' field."""
        buffer = ResponseBuffer()

        buffer.add_products([{"id": "1", "name": "Protein A"}])
        buffer.add_products([{"id": "1", "name": "Protein B"}])  # Duplicate!
        buffer.add_products([{"id": "2", "name": "Creatine"}])

        assert buffer.get_product_count() == 2
        # First one should be kept
        products = buffer.get_products()
        assert products[0]["name"] == "Protein A"

    def test_add_products_deduplication_by_underscore_id(self):
        """Test products are deduplicated by '_id' field (MongoDB style)."""
        buffer = ResponseBuffer()

        buffer.add_products([{"_id": "abc123", "name": "Protein"}])
        buffer.add_products([{"_id": "abc123", "name": "Protein Copy"}])  # Duplicate!

        assert buffer.get_product_count() == 1

    def test_add_products_deduplication_by_product_id(self):
        """Test products are deduplicated by 'product_id' field."""
        buffer = ResponseBuffer()

        buffer.add_products([{"product_id": "p1", "name": "Protein"}])
        buffer.add_products([{"product_id": "p1", "name": "Protein Copy"}])  # Duplicate!

        assert buffer.get_product_count() == 1

    def test_add_products_without_id(self):
        """Test products without ID are always added."""
        buffer = ResponseBuffer()

        buffer.add_products([{"name": "Protein"}])
        buffer.add_products([{"name": "Protein"}])  # Same name but no ID

        # Both should be added (can't dedupe without ID)
        assert buffer.get_product_count() == 2

    def test_add_products_empty_list(self):
        """Test adding empty list returns 0."""
        buffer = ResponseBuffer()

        added = buffer.add_products([])

        assert added == 0
        assert buffer.get_product_count() == 0

    def test_add_products_mixed_id_types(self):
        """Test deduplication works with different ID field types."""
        buffer = ResponseBuffer()

        buffer.add_products([
            {"id": "1", "name": "A"},
            {"_id": "2", "name": "B"},
            {"product_id": "3", "name": "C"},
        ])

        assert buffer.get_product_count() == 3

    def test_has_products(self):
        """Test has_products method."""
        buffer = ResponseBuffer()
        assert buffer.has_products() is False

        buffer.add_products([{"id": "1", "name": "Test"}])
        assert buffer.has_products() is True

    def test_get_products_returns_copy(self):
        """Test get_products returns a copy, not reference."""
        buffer = ResponseBuffer()
        buffer.add_products([{"id": "1", "name": "Protein"}])

        products = buffer.get_products()
        products.append({"id": "2", "name": "Creatine"})  # Modify returned list

        # Original should be unchanged
        assert buffer.get_product_count() == 1


# =============================================================================
# TIP EXTRACTION
# =============================================================================

class TestTipExtraction:
    """Tests for TIP extraction logic."""

    def test_extract_tip_basic(self):
        """Test basic TIP extraction."""
        buffer = ResponseBuffer()
        buffer.set_text("Some text [TIP]This is a tip[/TIP] more text")

        tip = buffer.extract_and_set_tip()

        assert tip == "This is a tip"
        assert buffer.get_tip() == "This is a tip"
        assert buffer.get_tip_source() == "native"

    def test_extract_tip_removes_from_text(self):
        """Test TIP is removed from text after extraction."""
        buffer = ResponseBuffer()
        buffer.set_text("Before [TIP]Tip content[/TIP] After")

        buffer.extract_and_set_tip()

        assert "[TIP]" not in buffer.get_text()
        assert "[/TIP]" not in buffer.get_text()
        assert "Before" in buffer.get_text()
        assert "After" in buffer.get_text()

    def test_extract_tip_multiline(self):
        """Test multiline TIP extraction."""
        buffer = ResponseBuffer()
        buffer.set_text("""
Text before
[TIP]
Line 1
Line 2
Line 3
[/TIP]
Text after
""")

        tip = buffer.extract_and_set_tip()

        assert "Line 1" in tip
        assert "Line 2" in tip
        assert "Line 3" in tip

    def test_extract_tip_case_insensitive(self):
        """Test TIP extraction is case insensitive."""
        buffer = ResponseBuffer()
        buffer.set_text("Text [tip]lowercase tip[/tip] more")

        tip = buffer.extract_and_set_tip()

        assert tip == "lowercase tip"

    def test_extract_tip_no_tip_present(self):
        """Test extraction when no TIP in text."""
        buffer = ResponseBuffer()
        buffer.set_text("Just regular text without any tips")

        tip = buffer.extract_and_set_tip()

        assert tip is None
        assert buffer.get_tip() is None

    def test_extract_tip_idempotent(self):
        """Test extraction is idempotent (only happens once)."""
        buffer = ResponseBuffer()
        buffer.set_text("Text [TIP]First tip[/TIP] more")

        tip1 = buffer.extract_and_set_tip()
        tip2 = buffer.extract_and_set_tip()  # Should return cached

        assert tip1 == tip2 == "First tip"

    def test_set_generated_tip_when_no_native(self):
        """Test setting generated tip when no native tip."""
        buffer = ResponseBuffer()
        buffer.set_text("Text without tip")
        buffer.extract_and_set_tip()  # No TIP found

        result = buffer.set_generated_tip("Generated advice")

        assert result is True
        assert buffer.get_tip() == "Generated advice"
        assert buffer.get_tip_source() == "generated"

    def test_set_generated_tip_blocked_by_native(self):
        """Test generated tip is blocked when native tip exists."""
        buffer = ResponseBuffer()
        buffer.set_text("Text [TIP]Native tip[/TIP] more")
        buffer.extract_and_set_tip()

        result = buffer.set_generated_tip("Generated advice")

        assert result is False
        assert buffer.get_tip() == "Native tip"  # Unchanged
        assert buffer.get_tip_source() == "native"

    def test_has_tip(self):
        """Test has_tip method."""
        buffer = ResponseBuffer()
        assert buffer.has_tip() is False

        buffer.set_text("[TIP]Test[/TIP]")
        buffer.extract_and_set_tip()

        assert buffer.has_tip() is True


# =============================================================================
# QUICK REPLIES PARSING
# =============================================================================

class TestQuickRepliesParsing:
    """Tests for quick replies extraction."""

    def test_parse_quick_replies_basic(self):
        """Test basic quick replies extraction."""
        buffer = ResponseBuffer()
        buffer.set_text("""
Some text
[QUICK_REPLIES]
რა არის პროტეინი?
რომელი ვიტამინი ჯობია?
[/QUICK_REPLIES]
""")

        replies = buffer.parse_quick_replies()

        assert len(replies) == 2
        assert replies[0]["title"] == "რა არის პროტეინი?"
        assert replies[0]["payload"] == "რა არის პროტეინი?"

    def test_parse_quick_replies_removes_from_text(self):
        """Test quick replies block is removed from text."""
        buffer = ResponseBuffer()
        buffer.set_text("Before [QUICK_REPLIES]Option 1[/QUICK_REPLIES] After")

        buffer.parse_quick_replies()

        assert "[QUICK_REPLIES]" not in buffer.get_text()
        assert "Before" in buffer.get_text()
        assert "After" in buffer.get_text()

    def test_parse_quick_replies_bullet_points(self):
        """Test parsing with bullet point format."""
        buffer = ResponseBuffer()
        buffer.set_text("""
[QUICK_REPLIES]
- Option one
* Option two
• Option three
[/QUICK_REPLIES]
""")

        replies = buffer.parse_quick_replies()

        assert len(replies) == 3
        assert replies[0]["title"] == "Option one"

    def test_parse_quick_replies_numbered(self):
        """Test parsing with numbered list format."""
        buffer = ResponseBuffer()
        buffer.set_text("""
[QUICK_REPLIES]
1. First option
2. Second option
3. Third option
[/QUICK_REPLIES]
""")

        replies = buffer.parse_quick_replies()

        assert len(replies) == 3
        assert replies[0]["title"] == "First option"

    def test_parse_quick_replies_fallback_pattern(self):
        """Test fallback Georgian pattern."""
        buffer = ResponseBuffer()
        buffer.set_text("""
Some response text

შემდეგი ნაბიჯი:
პროტეინი; კრეატინი; ვიტამინი
""")

        replies = buffer.parse_quick_replies()

        assert len(replies) >= 1

    def test_parse_quick_replies_max_four(self):
        """Test replies are limited to 4."""
        buffer = ResponseBuffer()
        buffer.set_text("""
[QUICK_REPLIES]
Option 1
Option 2
Option 3
Option 4
Option 5
Option 6
[/QUICK_REPLIES]
""")

        replies = buffer.parse_quick_replies()

        assert len(replies) == 4

    def test_parse_quick_replies_idempotent(self):
        """Test parsing is idempotent."""
        buffer = ResponseBuffer()
        buffer.set_text("[QUICK_REPLIES]Test[/QUICK_REPLIES]")

        replies1 = buffer.parse_quick_replies()
        replies2 = buffer.parse_quick_replies()

        assert replies1 == replies2


# =============================================================================
# PRODUCT MARKDOWN
# =============================================================================

class TestProductMarkdown:
    """Tests for product markdown formatting."""

    def test_format_products_markdown_basic(self):
        """Test basic product markdown formatting."""
        buffer = ResponseBuffer()
        buffer.add_products([
            {"id": "1", "name": "Whey Protein", "brand": "ON", "price": 150},
            {"id": "2", "name": "Creatine", "brand": "Supspace", "price": 50},
        ])

        markdown = buffer.format_products_markdown()

        assert "**1. Whey Protein**" in markdown
        assert "ON" in markdown
        assert "₾150" in markdown
        assert "**2. Creatine**" in markdown

    def test_format_products_markdown_empty(self):
        """Test formatting with no products."""
        buffer = ResponseBuffer()

        markdown = buffer.format_products_markdown()

        assert markdown == ""

    def test_format_products_markdown_max_ten(self):
        """Test markdown is limited to 10 products."""
        buffer = ResponseBuffer()
        products = [{"id": str(i), "name": f"Product {i}"} for i in range(15)]
        buffer.add_products(products)

        markdown = buffer.format_products_markdown()

        # Should only have 10 numbered items
        assert "**10." in markdown
        assert "**11." not in markdown

    def test_has_valid_product_markdown_true(self):
        """Test detection of valid product markdown."""
        buffer = ResponseBuffer()
        buffer.set_text("""
აი შენთვის შესაფერისი პროდუქტები:

**1. Whey Protein** - ON - ₾150
**2. Creatine** - Supspace - ₾50
""")

        assert buffer.has_valid_product_markdown() is True

    def test_has_valid_product_markdown_false(self):
        """Test detection when no valid product markdown."""
        buffer = ResponseBuffer()
        buffer.set_text("Just regular text without product formatting")

        assert buffer.has_valid_product_markdown() is False


# =============================================================================
# SNAPSHOT & STATE
# =============================================================================

class TestSnapshotAndState:
    """Tests for snapshot and state management."""

    def test_snapshot_returns_immutable(self):
        """Test snapshot returns immutable state."""
        buffer = ResponseBuffer()
        buffer.set_text("Test text")
        buffer.add_products([{"id": "1", "name": "Protein"}])

        snapshot = buffer.snapshot()

        # BufferState is frozen dataclass
        assert snapshot.text == "Test text"
        assert len(snapshot.products) == 1
        assert snapshot.has_content is True

    def test_snapshot_is_copy(self):
        """Test snapshot is independent of buffer."""
        buffer = ResponseBuffer()
        buffer.set_text("Original")

        snapshot1 = buffer.snapshot()

        buffer.set_text("Modified")
        snapshot2 = buffer.snapshot()

        assert snapshot1.text == "Original"
        assert snapshot2.text == "Modified"

    def test_has_content_with_text(self):
        """Test has_content with text only."""
        buffer = ResponseBuffer()
        buffer.set_text("Some text")

        assert buffer.has_content() is True

    def test_has_content_with_products(self):
        """Test has_content with products only."""
        buffer = ResponseBuffer()
        buffer.add_products([{"id": "1", "name": "Test"}])

        assert buffer.has_content() is True

    def test_has_content_empty(self):
        """Test has_content when empty."""
        buffer = ResponseBuffer()

        assert buffer.has_content() is False

    def test_clear_resets_all(self):
        """Test clear resets all state."""
        buffer = ResponseBuffer()
        buffer.set_text("Text")
        buffer.add_products([{"id": "1", "name": "Product"}])
        buffer.set_text("[TIP]Tip[/TIP]")
        buffer.extract_and_set_tip()

        buffer.clear()

        assert buffer.get_text() == ""
        assert buffer.get_product_count() == 0
        assert buffer.get_tip() is None

    def test_finalize_extracts_all(self):
        """Test finalize extracts TIP and quick replies."""
        buffer = ResponseBuffer()
        buffer.set_text("""
Text content [TIP]Tip here[/TIP]

[QUICK_REPLIES]
Option 1
Option 2
[/QUICK_REPLIES]
""")

        text, tip, replies = buffer.finalize()

        assert "[TIP]" not in text
        assert "[QUICK_REPLIES]" not in text
        assert tip == "Tip here"
        assert len(replies) == 2


# =============================================================================
# THREAD SAFETY
# =============================================================================

class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_text_append(self):
        """Test concurrent text appending doesn't corrupt data."""
        buffer = ResponseBuffer()
        errors = []

        def append_text(text: str, count: int):
            try:
                for _ in range(count):
                    buffer.append_text(text)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=append_text, args=("A", 100)),
            threading.Thread(target=append_text, args=("B", 100)),
            threading.Thread(target=append_text, args=("C", 100)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Total should be 300 characters (100 of each)
        text = buffer.get_text()
        assert text.count("A") == 100
        assert text.count("B") == 100
        assert text.count("C") == 100

    def test_concurrent_product_add(self):
        """Test concurrent product adding with deduplication."""
        buffer = ResponseBuffer()
        errors = []

        def add_products(prefix: str, count: int):
            try:
                for i in range(count):
                    buffer.add_products([{"id": f"{prefix}{i}", "name": f"Product {prefix}{i}"}])
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_products, args=("A", 50)),
            threading.Thread(target=add_products, args=("B", 50)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Should have 100 unique products
        assert buffer.get_product_count() == 100

    def test_concurrent_snapshot(self):
        """Test snapshots are consistent during concurrent modification."""
        buffer = ResponseBuffer()
        snapshots = []
        errors = []

        def modify_buffer():
            try:
                for i in range(100):
                    buffer.append_text(f"Text{i}")
                    buffer.add_products([{"id": str(i), "name": f"P{i}"}])
            except Exception as e:
                errors.append(e)

        def take_snapshots():
            try:
                for _ in range(100):
                    s = buffer.snapshot()
                    snapshots.append(s)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=modify_buffer),
            threading.Thread(target=take_snapshots),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(snapshots) == 100
        # Each snapshot should have consistent state
        for s in snapshots:
            assert isinstance(s, BufferState)


# =============================================================================
# GET CLEAN TEXT
# =============================================================================

class TestGetCleanText:
    """Tests for get_clean_text convenience method."""

    def test_get_clean_text_extracts_all(self):
        """Test get_clean_text extracts TIP and quick replies."""
        buffer = ResponseBuffer()
        buffer.set_text("""
Main content here.

[TIP]
This is a tip.
[/TIP]

[QUICK_REPLIES]
Option A
Option B
[/QUICK_REPLIES]
""")

        clean = buffer.get_clean_text()

        assert "Main content" in clean
        assert "[TIP]" not in clean
        assert "[QUICK_REPLIES]" not in clean

        # TIP and replies should be stored
        assert buffer.get_tip() == "This is a tip."
        assert len(buffer.get_quick_replies()) == 2
