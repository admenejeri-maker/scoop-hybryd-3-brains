"""
Scoop AI Tool Executor (v2.0)
=============================

Wrapper for tool function execution with explicit context passing.

This module solves Bug #14 from v1.0 by eliminating ContextVar dependency:
- v1.0: Used ContextVar for user_id, which failed with asyncio.to_thread
- v2.0: Passes user_id explicitly to all tool functions

Key Features:
1. Explicit parameter passing (no magic context propagation)
2. Pre-cached user profile (avoids parallel FC signature issue)
3. Query deduplication tracking
4. Works in any execution context (async, sync, thread pool)

Design Principle: Explicit is better than implicit (Python Zen).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Awaitable

from .types import FunctionCall, LoopState

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """
    Result from a tool execution.

    Encapsulates the function response and any extracted data.
    """
    name: str
    response: Dict[str, Any]
    products: List[Dict[str, Any]] = field(default_factory=list)
    skipped: bool = False
    skip_reason: Optional[str] = None


class ToolExecutor:
    """
    Executes tool functions with explicit context.

    REPLACES: The ContextVar-based tool execution in v1.0.

    v1.0 PROBLEM (Bug #14):
        _current_user_id = ContextVar('current_user_id')

        def search_products(query):
            user_id = _current_user_id.get()  # Returns None in thread pool!

    v2.0 SOLUTION:
        class ToolExecutor:
            def __init__(self, user_id: str):
                self.user_id = user_id

            async def search_products(self, query):
                return await _search_products(query, user_id=self.user_id)

    USAGE:
        executor = ToolExecutor(
            user_id="user123",
            user_profile={"name": "John", ...},  # Pre-cached
            search_fn=search_products,
            profile_fn=get_user_profile,
            update_profile_fn=update_user_profile,
            product_details_fn=get_product_details,
        )

        result = await executor.execute(FunctionCall(name="search_products", args={"query": "protein"}))
    """

    def __init__(
        self,
        user_id: str,
        user_profile: Optional[Dict[str, Any]] = None,
        # Tool function references (injected for testability)
        search_fn: Optional[Callable] = None,
        profile_fn: Optional[Callable] = None,
        update_profile_fn: Optional[Callable] = None,
        product_details_fn: Optional[Callable] = None,
        # Configuration
        max_unique_queries: int = 3,
    ):
        """
        Initialize ToolExecutor with explicit context.

        Args:
            user_id: User identifier (REQUIRED, no ContextVar lookup)
            user_profile: Pre-cached user profile (avoids parallel FC issue)
            search_fn: Reference to search_products function
            profile_fn: Reference to get_user_profile function
            update_profile_fn: Reference to update_user_profile function
            product_details_fn: Reference to get_product_details function
            max_unique_queries: Maximum unique search queries allowed
        """
        if not user_id:
            raise ValueError("user_id is required (no ContextVar fallback)")

        self.user_id = user_id
        self.user_profile = user_profile or {}

        # Tool function references
        self._search_fn = search_fn
        self._profile_fn = profile_fn
        self._update_profile_fn = update_profile_fn
        self._product_details_fn = product_details_fn

        # Deduplication state
        self._executed_queries: set = set()
        self._max_unique_queries = max_unique_queries

        # Results tracking
        self._all_products: List[Dict[str, Any]] = []

        logger.info(f"ToolExecutor initialized for user_id={user_id}")

    # =========================================================================
    # MAIN EXECUTION INTERFACE
    # =========================================================================

    async def execute(self, call: FunctionCall) -> ToolResult:
        """
        Execute a single function call.

        Routes to appropriate handler based on function name.
        Passes user_id explicitly to functions that need it.

        Args:
            call: FunctionCall with name and args

        Returns:
            ToolResult with response and extracted data
        """
        logger.info(f"🔧 Executing: {call.name}({call.args})")

        try:
            match call.name:
                case "search_products":
                    return await self._execute_search(call.args)

                case "get_user_profile":
                    return self._execute_get_profile()

                case "update_user_profile":
                    return await self._execute_update_profile(call.args)

                case "get_product_details":
                    return await self._execute_product_details(call.args)

                case _:
                    logger.warning(f"Unknown function: {call.name}")
                    return ToolResult(
                        name=call.name,
                        response={"error": f"Unknown function: {call.name}"},
                    )

        except Exception as e:
            logger.error(f"Tool execution error: {call.name} - {e}", exc_info=True)
            return ToolResult(
                name=call.name,
                response={"error": str(e)},
            )

    async def execute_batch(
        self,
        calls: List[FunctionCall],
        dedupe_search: bool = True,
    ) -> List[ToolResult]:
        """
        Execute multiple function calls.

        Optionally deduplicates search_products calls (only first is executed).

        Args:
            calls: List of FunctionCalls to execute
            dedupe_search: If True, only execute first search_products per batch

        Returns:
            List of ToolResults in same order as input calls
        """
        results = []
        search_executed_in_batch = False

        for call in calls:
            # Batch-level deduplication for search_products
            if dedupe_search and call.name == "search_products":
                if search_executed_in_batch:
                    logger.warning(f"⚠️ Skipping duplicate search_products in batch")
                    results.append(ToolResult(
                        name=call.name,
                        response={"note": "Skipped duplicate search in batch"},
                        skipped=True,
                        skip_reason="batch_duplicate",
                    ))
                    continue
                search_executed_in_batch = True

            result = await self.execute(call)
            results.append(result)

        return results

    # =========================================================================
    # TOOL IMPLEMENTATIONS
    # =========================================================================

    async def _execute_search(self, args: Dict[str, Any]) -> ToolResult:
        """
        Execute search_products with deduplication and explicit user_id.

        Deduplication Rules:
        1. Skip if same query already executed
        2. Skip if max unique queries reached

        Args:
            args: Function arguments (query, max_price, etc.)

        Returns:
            ToolResult with products or skip info
        """
        query = args.get("query", "").strip()
        query_key = query.lower()

        # Check for duplicate query
        if query_key in self._executed_queries:
            logger.warning(f"⚠️ Skipping duplicate query: '{query}'")
            return ToolResult(
                name="search_products",
                response={
                    "products": self._all_products,
                    "count": len(self._all_products),
                    "note": f"Duplicate query '{query}', returning cached results",
                },
                products=self._all_products,
                skipped=True,
                skip_reason="duplicate_query",
            )

        # Check query limit
        if len(self._executed_queries) >= self._max_unique_queries:
            logger.warning(f"⚠️ Query limit reached ({self._max_unique_queries})")
            return ToolResult(
                name="search_products",
                response={
                    "products": self._all_products,
                    "count": len(self._all_products),
                    # CRITICAL: Forceful directive to stop searching - BUG FIX v2.0
                    "status": "SEARCH_COMPLETE",
                    "instruction": (
                        f"⛔ საძიებო ლიმიტი ამოიწურა. "
                        f"ნაპოვნია {len(self._all_products)} პროდუქტი. "
                        f"აღარ გამოიძახო search_products! "
                        f"დაწერე რეკომენდაცია ახლავე ამ პროდუქტების საფუძველზე."
                    ),
                },
                products=self._all_products,
                skipped=True,
                skip_reason="query_limit",
            )

        # Execute actual search
        if self._search_fn is None:
            return ToolResult(
                name="search_products",
                response={"error": "Search function not configured"},
            )

        # Mark query as executed BEFORE calling (prevents race conditions)
        self._executed_queries.add(query_key)

        # Call search with explicit user_id
        # Note: search_fn should accept user_id parameter in v2.0
        result = await self._call_search_fn(args)

        # Extract and track products
        products = result.get("products", [])
        if products:
            self._all_products.extend(products)
            logger.info(f"✅ Search found {len(products)} products (total: {len(self._all_products)})")

        return ToolResult(
            name="search_products",
            response=result,
            products=products,
        )

    async def _call_search_fn(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the search function with appropriate async handling.

        Handles both sync and async search functions.

        Args:
            args: Search arguments

        Returns:
            Search result dict
        """
        import asyncio
        import inspect

        # Add user_id to args (v2.0 signature)
        search_args = {**args, "user_id": self.user_id}

        if inspect.iscoroutinefunction(self._search_fn):
            # Async function
            return await self._search_fn(**search_args)
        else:
            # Sync function - run in thread pool
            # NOTE: We pass user_id explicitly, so no ContextVar needed!
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self._search_fn(**search_args)
            )

    def _execute_get_profile(self) -> ToolResult:
        """
        Return pre-cached user profile.

        This avoids the parallel FC signature issue from v1.0:
        When Gemini sends parallel FCs, only first gets thought_signature.
        By pre-caching profile, we eliminate get_user_profile FC entirely.

        Returns:
            ToolResult with cached profile
        """
        logger.info(f"📦 Using pre-cached profile for user: {self.user_id}")

        return ToolResult(
            name="get_user_profile",
            response=self.user_profile,
        )

    async def _execute_update_profile(self, args: Dict[str, Any]) -> ToolResult:
        """
        Execute update_user_profile with explicit user_id.

        Args:
            args: Profile update arguments

        Returns:
            ToolResult with update response
        """
        if self._update_profile_fn is None:
            return ToolResult(
                name="update_user_profile",
                response={"error": "Update function not configured"},
            )

        # Add user_id to args
        update_args = {**args, "user_id": self.user_id}

        result = await self._call_async_or_sync(
            self._update_profile_fn,
            update_args
        )

        # Update cached profile with new values
        if "error" not in result:
            self.user_profile.update(args)

        return ToolResult(
            name="update_user_profile",
            response=result,
        )

    async def _execute_product_details(self, args: Dict[str, Any]) -> ToolResult:
        """
        Execute get_product_details.

        Args:
            args: Product details arguments (product_id)

        Returns:
            ToolResult with product details
        """
        if self._product_details_fn is None:
            return ToolResult(
                name="get_product_details",
                response={"error": "Product details function not configured"},
            )

        result = await self._call_async_or_sync(
            self._product_details_fn,
            args
        )

        return ToolResult(
            name="get_product_details",
            response=result,
        )

    async def _call_async_or_sync(
        self,
        fn: Callable,
        args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Call a function handling both async and sync cases.

        Args:
            fn: Function to call
            args: Arguments to pass

        Returns:
            Function result
        """
        import asyncio
        import inspect

        if inspect.iscoroutinefunction(fn):
            return await fn(**args)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: fn(**args))

    # =========================================================================
    # STATE ACCESS
    # =========================================================================

    def get_all_products(self) -> List[Dict[str, Any]]:
        """
        Get all products found across all searches.

        Returns:
            List of all products
        """
        return self._all_products.copy()

    def get_executed_queries(self) -> set:
        """
        Get set of executed queries.

        Returns:
            Set of query strings (lowercase)
        """
        return self._executed_queries.copy()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get execution statistics.

        Returns:
            Dict with execution stats
        """
        return {
            "user_id": self.user_id,
            "unique_queries": len(self._executed_queries),
            "total_products": len(self._all_products),
            "queries": list(self._executed_queries),
        }

    # =========================================================================
    # FACTORY METHOD
    # =========================================================================

    @classmethod
    def create_with_defaults(
        cls,
        user_id: str,
        user_profile: Optional[Dict[str, Any]] = None,
    ) -> "ToolExecutor":
        """
        Create ToolExecutor with default tool functions from user_tools.

        This is the convenience factory for production use.

        Args:
            user_id: User identifier
            user_profile: Pre-cached user profile

        Returns:
            Configured ToolExecutor
        """
        # Import here to avoid circular imports
        from app.tools.user_tools import (
            search_products,
            get_user_profile,
            update_user_profile,
            get_product_details,
        )

        return cls(
            user_id=user_id,
            user_profile=user_profile,
            search_fn=search_products,
            profile_fn=get_user_profile,
            update_profile_fn=update_user_profile,
            product_details_fn=get_product_details,
        )
