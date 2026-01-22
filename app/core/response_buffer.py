"""
Scoop AI Response Buffer (v2.0)
===============================

Thread-safe accumulator for response components.

This module replaces the scattered state variables in v1.0's chat_stream function:
- accumulated_text
- search_products_results
- native_tip_sent
- etc.

Key Features:
1. Atomic operations (no race conditions between SSE yields)
2. TIP extraction happens ONCE, not in multiple places
3. Product deduplication built-in
4. Immutable snapshots for safe SSE yields

Design Principle: Encapsulate all response state in one place.
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class BufferState:
    """
    Immutable snapshot of buffer state.

    Used for SSE yields to avoid race conditions.
    The frozen=True ensures this object cannot be modified after creation.
    """
    text: str
    products: List[Dict[str, Any]]
    tip: Optional[str]
    tip_source: Optional[str]  # "native" | "generated" | None
    quick_replies: List[Dict[str, str]]
    product_count: int
    has_content: bool


class ResponseBuffer:
    """
    Thread-safe accumulator for response components.

    REPLACES: Scattered state variables in main.py:
    - accumulated_text (L2253)
    - search_products_results (L2254)
    - native_tip_sent (L2259)
    - thought_texts_collected (L2258) - NOT REPLACED, thoughts no longer used for fallback

    USAGE:
        buffer = ResponseBuffer()
        buffer.append_text("Hello ")
        buffer.append_text("world!")
        buffer.add_products([{"id": "1", "name": "Protein"}])
        buffer.extract_and_set_tip()  # Extracts [TIP]...[/TIP] from text

        snapshot = buffer.snapshot()  # Get immutable state for SSE yield
        yield {"type": "text", "content": snapshot.text}

    THREAD SAFETY:
        All public methods acquire a lock before modifying state.
        This prevents race conditions when SSE yields interleave with state updates.
    """

    # Regex pattern for TIP extraction
    TIP_PATTERN = re.compile(r'\[TIP\](.*?)\[/TIP\]', re.DOTALL | re.IGNORECASE)

    # Regex pattern for QUICK_REPLIES extraction
    QUICK_REPLIES_PATTERN = re.compile(
        r'\[QUICK_REPLIES\](.*?)\[/QUICK_REPLIES\]',
        re.DOTALL | re.IGNORECASE
    )

    # Fallback pattern for quick replies (Georgian format)
    QUICK_REPLIES_FALLBACK_PATTERN = re.compile(
        r'შემდეგი ნაბიჯი[:\s]*(.+?)(?=\n\n|\[|\Z)',
        re.DOTALL | re.IGNORECASE
    )

    def __init__(self) -> None:
        """Initialize empty buffer with thread lock."""
        self._lock = threading.RLock()  # Reentrant lock for nested calls

        # Core state
        self._text: str = ""
        self._products: List[Dict[str, Any]] = []
        self._tip: Optional[str] = None
        self._tip_source: Optional[str] = None
        self._quick_replies: List[Dict[str, str]] = []

        # Deduplication tracking
        self._product_ids: set = set()

        # Extraction flags
        self._tip_extracted: bool = False
        self._quick_replies_extracted: bool = False

    # =========================================================================
    # TEXT OPERATIONS
    # =========================================================================

    def append_text(self, text: str) -> None:
        """
        Append text to buffer (for streaming accumulation).

        Thread-safe: acquires lock before modification.

        Args:
            text: Text chunk to append
        """
        if not text:
            return

        with self._lock:
            self._text += text

    def set_text(self, text: str) -> None:
        """
        Set complete text (for non-streaming or replacement).

        Thread-safe: acquires lock before modification.

        Args:
            text: Complete text to set
        """
        with self._lock:
            self._text = text or ""
            # Reset extraction flags since text changed
            self._tip_extracted = False
            self._quick_replies_extracted = False

    def get_text(self) -> str:
        """
        Get current text content.

        Thread-safe: acquires lock before read.

        Returns:
            Current accumulated text
        """
        with self._lock:
            return self._text

    def has_text(self) -> bool:
        """
        Check if buffer has meaningful text content.

        Thread-safe: acquires lock before read.

        Returns:
            True if text is non-empty after stripping
        """
        with self._lock:
            return bool(self._text.strip())

    # =========================================================================
    # PRODUCT OPERATIONS
    # =========================================================================

    def add_products(self, products: List[Dict[str, Any]]) -> int:
        """
        Add products with deduplication.

        Thread-safe: acquires lock before modification.

        Deduplication is based on product ID (checks 'id', '_id', 'product_id').
        Products without IDs are always added (cannot dedupe).

        Args:
            products: List of product dicts to add

        Returns:
            Number of new products actually added (after deduplication)
        """
        if not products:
            return 0

        with self._lock:
            added = 0
            for product in products:
                # Try multiple ID field names
                pid = (
                    product.get("id") or
                    product.get("_id") or
                    product.get("product_id")
                )

                if pid:
                    pid_str = str(pid)
                    if pid_str not in self._product_ids:
                        self._product_ids.add(pid_str)
                        self._products.append(product)
                        added += 1
                else:
                    # No ID, add anyway (can't dedupe)
                    self._products.append(product)
                    added += 1

            return added

    def get_products(self) -> List[Dict[str, Any]]:
        """
        Get current products list.

        Thread-safe: acquires lock and returns copy.

        Returns:
            Copy of current products list
        """
        with self._lock:
            return self._products.copy()

    def get_product_count(self) -> int:
        """
        Get number of products in buffer.

        Thread-safe: acquires lock before read.

        Returns:
            Number of products
        """
        with self._lock:
            return len(self._products)

    def has_products(self) -> bool:
        """
        Check if buffer has any products.

        Thread-safe: acquires lock before read.

        Returns:
            True if products list is non-empty
        """
        with self._lock:
            return bool(self._products)

    # =========================================================================
    # TIP OPERATIONS
    # =========================================================================

    def extract_and_set_tip(self) -> Optional[str]:
        """
        Extract [TIP]...[/TIP] from text if present, set as native tip.

        This is the SINGLE EXTRACTION POINT for TIPs.
        Replaces multiple extract_tip_from_text() calls in v1.0.

        Thread-safe: acquires lock before modification.

        The extracted TIP is:
        1. Stored in self._tip
        2. Removed from self._text
        3. Marked as "native" source

        Returns:
            Extracted tip content, or None if no TIP found
        """
        with self._lock:
            if self._tip_extracted:
                return self._tip  # Already extracted

            match = self.TIP_PATTERN.search(self._text)

            if match:
                self._tip = match.group(1).strip()
                self._tip_source = "native"
                # Remove TIP from text
                self._text = self.TIP_PATTERN.sub('', self._text).strip()
                self._tip_extracted = True
                return self._tip

            self._tip_extracted = True  # Mark as attempted even if not found
            return None

    def set_generated_tip(self, tip: str) -> bool:
        """
        Set a generated tip (only if no native tip exists).

        Thread-safe: acquires lock before modification.

        Args:
            tip: The generated tip content

        Returns:
            True if tip was set, False if native tip already exists
        """
        if not tip:
            return False

        with self._lock:
            if self._tip is None:
                self._tip = tip
                self._tip_source = "generated"
                return True
            return False

    def get_tip(self) -> Optional[str]:
        """
        Get current tip content.

        Thread-safe: acquires lock before read.

        Returns:
            Tip content or None
        """
        with self._lock:
            return self._tip

    def has_tip(self) -> bool:
        """
        Check if buffer has a tip (native or generated).

        Thread-safe: acquires lock before read.

        Returns:
            True if tip exists
        """
        with self._lock:
            return self._tip is not None

    def get_tip_source(self) -> Optional[str]:
        """
        Get tip source ("native" or "generated").

        Thread-safe: acquires lock before read.

        Returns:
            Tip source string or None
        """
        with self._lock:
            return self._tip_source

    # =========================================================================
    # QUICK REPLIES OPERATIONS
    # =========================================================================

    def parse_quick_replies(self) -> List[Dict[str, str]]:
        """
        Extract [QUICK_REPLIES] block from text.

        Thread-safe: acquires lock before modification.

        Tries two patterns:
        1. [QUICK_REPLIES]...[/QUICK_REPLIES] block
        2. "შემდეგი ნაბიჯი:" Georgian format

        The extracted replies are:
        1. Stored in self._quick_replies
        2. Removed from self._text

        Returns:
            List of quick reply dicts with 'title' and 'payload' keys
        """
        with self._lock:
            if self._quick_replies_extracted:
                return self._quick_replies.copy()

            replies = []

            # Try primary pattern first
            match = self.QUICK_REPLIES_PATTERN.search(self._text)
            if match:
                content = match.group(1).strip()
                replies = self._parse_reply_content(content)
                # Remove from text
                self._text = self.QUICK_REPLIES_PATTERN.sub('', self._text).strip()
            else:
                # Try fallback Georgian pattern
                match = self.QUICK_REPLIES_FALLBACK_PATTERN.search(self._text)
                if match:
                    content = match.group(1).strip()
                    replies = self._parse_reply_content(content)
                    # Remove from text
                    self._text = self.QUICK_REPLIES_FALLBACK_PATTERN.sub('', self._text).strip()

            self._quick_replies = replies
            self._quick_replies_extracted = True
            return replies.copy()

    def _parse_reply_content(self, content: str) -> List[Dict[str, str]]:
        """
        Parse quick reply content into structured list.

        Handles various formats:
        - Bullet points (-, *, •)
        - Numbered lists (1., 2.)
        - Newline separated

        Args:
            content: Raw content from quick replies block

        Returns:
            List of dicts with 'title' and 'payload' keys
        """
        replies = []

        # Split by newlines and common separators
        lines = re.split(r'[\n;]', content)

        for line in lines:
            # Clean up the line
            line = re.sub(r'^[\s\-\*•\d.]+', '', line).strip()

            if line and len(line) > 2:  # Skip very short lines
                # Limit length for UI
                title = line[:100] if len(line) > 100 else line
                replies.append({
                    "title": title,
                    "payload": title  # Same as title for now
                })

        # Limit to 4 replies
        return replies[:4]

    def get_quick_replies(self) -> List[Dict[str, str]]:
        """
        Get current quick replies.

        Thread-safe: acquires lock and returns copy.

        Returns:
            Copy of quick replies list
        """
        with self._lock:
            return self._quick_replies.copy()

    def set_quick_replies(self, replies: List[Dict[str, str]]) -> None:
        """
        Set quick replies directly (for external generation).

        Thread-safe: acquires lock before modification.

        Args:
            replies: List of quick reply dicts
        """
        with self._lock:
            self._quick_replies = replies[:4] if replies else []  # Limit to 4
            self._quick_replies_extracted = True

    # =========================================================================
    # PRODUCT MARKDOWN FORMATTING
    # =========================================================================

    def format_products_markdown(self) -> str:
        """
        Format products as markdown for response.

        Thread-safe: acquires lock before read.

        Returns:
            Markdown formatted product list
        """
        with self._lock:
            if not self._products:
                return ""

            lines = []
            for i, product in enumerate(self._products[:10], 1):  # Limit to 10
                name = product.get("name", "პროდუქტი")
                price = product.get("price", 0)
                brand = product.get("brand", "")

                # Format: **1. Product Name** - Brand - ₾XX
                line = f"**{i}. {name}**"
                if brand:
                    line += f" - {brand}"
                if price:
                    line += f" - ₾{price}"

                lines.append(line)

            return "\n".join(lines)

    def has_valid_product_markdown(self) -> bool:
        """
        Check if text already contains valid product markdown.

        Looks for patterns like "**1." or "**პროდუქტი**".

        Thread-safe: acquires lock before read.

        Returns:
            True if text appears to have product formatting
        """
        with self._lock:
            if not self._text:
                return False

            # Check for numbered product format
            if re.search(r'\*\*\d+\.', self._text):
                return True

            # Check for at least 2 bold product names
            bold_matches = re.findall(r'\*\*[^*]+\*\*', self._text)
            return len(bold_matches) >= 2

    # =========================================================================
    # SNAPSHOT & STATE
    # =========================================================================

    def snapshot(self) -> BufferState:
        """
        Get immutable snapshot of current state.

        Thread-safe: acquires lock and creates frozen copy.

        Use this for SSE yields to avoid race conditions.

        Returns:
            Immutable BufferState with current values
        """
        with self._lock:
            return BufferState(
                text=self._text,
                products=self._products.copy(),
                tip=self._tip,
                tip_source=self._tip_source,
                quick_replies=self._quick_replies.copy(),
                product_count=len(self._products),
                has_content=bool(self._text.strip()) or bool(self._products),
            )

    def has_content(self) -> bool:
        """
        Check if buffer has any meaningful content (text or products).

        Thread-safe: acquires lock before read.

        Returns:
            True if buffer has text or products
        """
        with self._lock:
            return bool(self._text.strip()) or bool(self._products)

    def clear(self) -> None:
        """
        Clear all buffer state.

        Thread-safe: acquires lock before modification.

        Useful for resetting between requests in tests.
        """
        with self._lock:
            self._text = ""
            self._products = []
            self._tip = None
            self._tip_source = None
            self._quick_replies = []
            self._product_ids = set()
            self._tip_extracted = False
            self._quick_replies_extracted = False

    def get_clean_text(self) -> str:
        """
        Get text with TIP and QUICK_REPLIES extracted.

        Ensures extraction has been performed, then returns clean text.

        Thread-safe: calls extract methods which acquire locks.

        Returns:
            Text with TIP and QUICK_REPLIES removed
        """
        self.extract_and_set_tip()
        self.parse_quick_replies()
        return self.get_text()

    def finalize(self) -> Tuple[str, Optional[str], List[Dict[str, str]]]:
        """
        Finalize buffer: extract TIP, parse quick replies, return all.

        Convenience method for final response building.

        Thread-safe: calls extract methods which acquire locks.

        Returns:
            Tuple of (clean_text, tip, quick_replies)
        """
        self.extract_and_set_tip()
        self.parse_quick_replies()

        with self._lock:
            return (
                self._text,
                self._tip,
                self._quick_replies.copy()
            )
