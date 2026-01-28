"""
Unit tests for Tiered Memory System.

Tests:
1. High importance facts → curated_facts
2. Low importance facts → daily_facts with TTL
3. Backward compatibility with legacy user_facts
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# TIERED ROUTING TESTS
# =============================================================================

class TestTieredMemoryRouting:
    """Tests for tiered fact routing based on importance."""
    
    @pytest.mark.asyncio
    async def test_high_importance_fact_goes_to_curated(self):
        """Facts with importance >= 0.8 should go to curated_facts with $slice: -100."""
        from app.memory.mongo_store import UserStore, CURATED_IMPORTANCE_THRESHOLD

        store = UserStore()
        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(return_value=None)  # New user
        mock_collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        with patch.object(type(store), 'collection', new=mock_collection):
            result = await store.add_user_fact(
                user_id="user123",
                fact="ალერგია მაქვს ლაქტოზაზე",  # High importance - allergy
                embedding=[0.5] * 768,
                importance_score=0.9  # >= 0.8 threshold
            )

            assert result["status"] == "added"

            # Verify update_one was called with curated_facts
            call_args = mock_collection.update_one.call_args[0][1]
            assert "$push" in call_args

            # Should push to curated_facts with $slice
            push_target = list(call_args["$push"].keys())[0]
            assert push_target == "curated_facts", \
                f"Expected push to curated_facts but got: {push_target}"

            # Memory v2.2: Verify $slice limit
            push_spec = call_args["$push"]["curated_facts"]
            assert "$each" in push_spec, "Should use $each for $slice"
            assert "$slice" in push_spec, "Should have $slice limit"
            assert push_spec["$slice"] == -100, f"Expected $slice: -100, got: {push_spec['$slice']}"
    
    @pytest.mark.asyncio
    async def test_low_importance_fact_goes_to_daily_with_ttl(self):
        """Facts with importance < 0.8 should go to daily_facts with expires_at and $slice: -200."""
        from app.memory.mongo_store import UserStore, DAILY_FACTS_TTL_DAYS

        store = UserStore()
        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(return_value=None)  # New user
        mock_collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        before_call = datetime.utcnow()

        with patch.object(type(store), 'collection', new=mock_collection):
            result = await store.add_user_fact(
                user_id="user123",
                fact="კრეატინი შეიძინა დღეს",  # Lower importance
                embedding=[0.5] * 768,
                importance_score=0.5  # < 0.8 threshold
            )

            assert result["status"] == "added"

            # Verify update_one was called
            call_args = mock_collection.update_one.call_args[0][1]
            assert "$push" in call_args

            # Should push to daily_facts
            push_target = list(call_args["$push"].keys())[0]
            assert push_target == "daily_facts", \
                f"Expected push to daily_facts but got: {push_target}"

            # Memory v2.2: Verify $slice limit for daily_facts
            push_spec = call_args["$push"]["daily_facts"]
            assert "$each" in push_spec, "Should use $each for $slice"
            assert "$slice" in push_spec, "Should have $slice limit"
            assert push_spec["$slice"] == -200, f"Expected $slice: -200, got: {push_spec['$slice']}"

            # Verify expires_at field in the fact document
            fact_doc = push_spec["$each"][0]
            assert "expires_at" in fact_doc, "daily_facts should have expires_at field"

            # Should expire approximately 60 days in future
            expected_expiry = before_call + timedelta(days=DAILY_FACTS_TTL_DAYS)
            actual_expiry = fact_doc["expires_at"]

            # Allow 1 minute tolerance for test execution time
            delta = abs((actual_expiry - expected_expiry).total_seconds())
            assert delta < 60, f"Expiry should be ~{DAILY_FACTS_TTL_DAYS} days, got delta: {delta}s"
    
    @pytest.mark.asyncio
    async def test_threshold_boundary_goes_to_curated(self):
        """Facts with importance exactly 0.8 should go to curated_facts with $slice: -100."""
        from app.memory.mongo_store import UserStore

        store = UserStore()
        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(return_value=None)
        mock_collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

        with patch.object(type(store), 'collection', new=mock_collection):
            result = await store.add_user_fact(
                user_id="user123",
                fact="მნიშვნელოვანი ფაქტი ზუსტად ზღურბლზე",
                embedding=[0.5] * 768,
                importance_score=0.8  # Exactly at threshold
            )

            assert result["status"] == "added"
            call_args = mock_collection.update_one.call_args[0][1]
            push_target = list(call_args["$push"].keys())[0]
            assert push_target == "curated_facts"

            # Memory v2.2: Verify $slice limit
            push_spec = call_args["$push"]["curated_facts"]
            assert push_spec["$slice"] == -100


# =============================================================================
# BACKWARD COMPATIBILITY TESTS
# =============================================================================

class TestBackwardCompatibility:
    """Tests for backward compatibility with legacy user_facts."""
    
    @pytest.mark.asyncio
    async def test_get_relevant_facts_falls_back_to_legacy(self):
        """Should search user_facts if curated/daily are empty."""
        from app.memory.mongo_store import UserStore
        
        store = UserStore()
        mock_collection = AsyncMock()
        
        # Simulate old user with only legacy user_facts
        legacy_user = {
            "user_id": "old_user",
            "user_facts": [
                {
                    "fact": "მიყვარს ყავა დილით",
                    "embedding": [1.0] + [0.0] * 767,
                    "importance_score": 0.6,
                    "is_sensitive": False,
                    "created_at": datetime.utcnow()
                }
            ]
            # No curated_facts or daily_facts fields
        }
        mock_collection.find_one = AsyncMock(return_value=legacy_user)
        
        with patch.object(type(store), 'collection', new=mock_collection):
            results = await store.get_relevant_facts(
                user_id="old_user",
                query_embedding=[0.99] + [0.01] + [0.0] * 766,  # Similar to existing
                limit=5,
                min_similarity=0.5
            )
            
            assert len(results) == 1, f"Should find 1 fact, got: {len(results)}"
            assert "ყავა" in results[0]["fact"]
    
    @pytest.mark.asyncio
    async def test_get_relevant_facts_combines_all_sources(self):
        """Should search all three: curated, daily, and legacy user_facts."""
        from app.memory.mongo_store import UserStore
        
        store = UserStore()
        mock_collection = AsyncMock()
        
        # User with facts in all three locations
        mixed_user = {
            "user_id": "mixed_user",
            "curated_facts": [
                {
                    "fact": "ალერგია მაქვს თხილზე",
                    "embedding": [1.0, 0.0] + [0.0] * 766,
                    "importance_score": 0.9,
                    "is_sensitive": True,
                    "created_at": datetime.utcnow()
                }
            ],
            "daily_facts": [
                {
                    "fact": "გუშინ იოგა გავაკეთე",
                    "embedding": [0.0, 1.0] + [0.0] * 766,
                    "importance_score": 0.5,
                    "is_sensitive": False,
                    "created_at": datetime.utcnow(),
                    "expires_at": datetime.utcnow() + timedelta(days=60)
                }
            ],
            "user_facts": [
                {
                    "fact": "ძველი ფაქტი legacy-დან",
                    "embedding": [0.5, 0.5] + [0.0] * 766,
                    "importance_score": 0.6,
                    "is_sensitive": False,
                    "created_at": datetime.utcnow()
                }
            ]
        }
        mock_collection.find_one = AsyncMock(return_value=mixed_user)
        
        with patch.object(type(store), 'collection', new=mock_collection):
            # Query that should match all three
            results = await store.get_relevant_facts(
                user_id="mixed_user",
                query_embedding=[0.6, 0.6] + [0.0] * 766,
                limit=10,
                min_similarity=0.1  # Low threshold to catch all
            )
            
            # Should find facts from all sources
            assert len(results) >= 2, f"Should find facts from multiple sources, got: {len(results)}"
            
            # Verify we got facts from different sources
            fact_texts = [r["fact"] for r in results]
            has_curated = any("ალერგია" in f for f in fact_texts)
            has_daily_or_legacy = any("იოგა" in f or "ძველი" in f for f in fact_texts)
            
            assert has_curated or has_daily_or_legacy, \
                f"Should include facts from multiple sources. Got: {fact_texts}"
    
    @pytest.mark.asyncio
    async def test_curated_facts_prioritized_in_ranking(self):
        """Curated facts should appear before daily facts at similar similarity."""
        from app.memory.mongo_store import UserStore
        
        store = UserStore()
        mock_collection = AsyncMock()
        
        # Same embedding for both - curated should rank higher
        same_embedding = [1.0] + [0.0] * 767
        
        user_with_both = {
            "user_id": "test_user",
            "curated_facts": [
                {
                    "fact": "მნიშვნელოვანი curated ფაქტი",
                    "embedding": same_embedding,
                    "importance_score": 0.9,
                    "is_sensitive": False,
                    "created_at": datetime.utcnow()
                }
            ],
            "daily_facts": [
                {
                    "fact": "ნაკლებად მნიშვნელოვანი daily ფაქტი", 
                    "embedding": same_embedding,
                    "importance_score": 0.5,
                    "is_sensitive": False,
                    "created_at": datetime.utcnow()
                }
            ]
        }
        mock_collection.find_one = AsyncMock(return_value=user_with_both)
        
        with patch.object(type(store), 'collection', new=mock_collection):
            results = await store.get_relevant_facts(
                user_id="test_user",
                query_embedding=same_embedding,
                limit=5
            )
            
            # Both should be found
            assert len(results) == 2
            
            # Curated should be first (higher importance = tiebreaker)
            assert "curated" in results[0]["fact"], \
                f"Curated should rank first, got: {results[0]['fact']}"


# =============================================================================
# HYBRID SEARCH TESTS (Phase 2)
# =============================================================================

class TestHybridSearch:
    """Tests for hybrid vector + keyword scoring."""
    
    def test_keyword_score_logic(self):
        """Validate _keyword_score correctly calculates token overlap ratio."""
        from app.memory.mongo_store import UserStore
        
        store = UserStore()
        
        # Test 1: Full overlap
        score = store._keyword_score("creatine monohydrate", "creatine monohydrate powder")
        assert score >= 0.4, f"Expected >= 0.4 for 2/3 match, got {score}"
        
        # Test 2: Partial overlap
        score = store._keyword_score("creatine", "creatine monohydrate")
        assert score >= 0.4, f"Expected >= 0.4 for 1/2 match, got {score}"
        
        # Test 3: No overlap
        score = store._keyword_score("protein", "creatine monohydrate")
        assert score == 0.0, f"Expected 0 for no match, got {score}"
        
        # Test 4: Georgian text
        score = store._keyword_score("ლაქტოზა", "ალერგია ლაქტოზაზე")
        assert score > 0.0, f"Expected > 0 for partial Georgian match, got {score}"
    
    @pytest.mark.asyncio
    async def test_hybrid_ranking_favors_exact_match(self):
        """
        Fact B (medium vector, high keyword) should beat 
        Fact A (high vector, low keyword) with 0.7/0.3 weights.
        """
        from app.memory.mongo_store import UserStore
        
        store = UserStore()
        mock_collection = AsyncMock()
        
        # Setup: Two facts with different vector/keyword profiles
        # Fact A: embedding very similar to query, but different text
        # Fact B: embedding less similar, but exact keyword match
        query_embedding = [1.0] + [0.0] * 767
        
        user_data = {
            "user_id": "test_user",
            "curated_facts": [
                {
                    # Fact A: High vector score (0.95), low keyword overlap
                    "fact": "მას ძალიან უყვარს სპორტი და ფიტნესი",  # No "creatine"
                    "embedding": [0.98, 0.02] + [0.0] * 766,  # Very similar
                    "importance_score": 0.9,
                    "is_sensitive": False,
                    "created_at": datetime.utcnow()
                },
                {
                    # Fact B: Medium vector score (0.7), high keyword match
                    "fact": "Optimum Nutrition Creatine Monohydrate შეიძინა",
                    "embedding": [0.6, 0.5] + [0.0] * 766,  # Less similar
                    "importance_score": 0.85,
                    "is_sensitive": False,
                    "created_at": datetime.utcnow()
                }
            ],
            "daily_facts": [],
            "user_facts": []
        }
        mock_collection.find_one = AsyncMock(return_value=user_data)
        
        with patch.object(type(store), 'collection', mock_collection):
            results = await store.get_relevant_facts(
                user_id="test_user",
                query_embedding=query_embedding,
                query_text="creatine monohydrate",  # Exact keyword match for Fact B
                limit=5,
                min_similarity=0.1
            )
            
            assert len(results) >= 2, f"Expected 2 results, got {len(results)}"
            
            # Fact B (with "creatine monohydrate") should rank first
            # because: 0.7*0.7 + 0.3*1.0 = 0.49 + 0.3 = 0.79
            # vs Fact A: 0.7*0.95 + 0.3*0.0 = 0.665 + 0 = 0.665
            first_fact = results[0]["fact"]
            assert "Creatine" in first_fact or "creatine" in first_fact, \
                f"Fact B with keyword match should rank first, got: {first_fact}"
    
    @pytest.mark.asyncio
    async def test_hybrid_falls_back_to_vector_only(self):
        """When query_text is None, should use pure vector similarity."""
        from app.memory.mongo_store import UserStore
        
        store = UserStore()
        mock_collection = AsyncMock()
        
        query_embedding = [1.0, 0.0] + [0.0] * 766
        
        user_data = {
            "user_id": "test_user",
            "curated_facts": [
                {
                    "fact": "ფაქტი A - მაღალი ვექტორით",
                    "embedding": [0.99, 0.01] + [0.0] * 766,  # Very similar
                    "importance_score": 0.9,
                    "created_at": datetime.utcnow()
                },
                {
                    "fact": "ფაქტი B - დაბალი ვექტორით",
                    "embedding": [0.5, 0.5] + [0.0] * 766,  # Less similar
                    "importance_score": 0.9,
                    "created_at": datetime.utcnow()
                }
            ],
            "daily_facts": [],
            "user_facts": []
        }
        mock_collection.find_one = AsyncMock(return_value=user_data)
        
        with patch.object(type(store), 'collection', mock_collection):
            # No query_text - pure vector
            results = await store.get_relevant_facts(
                user_id="test_user",
                query_embedding=query_embedding,
                limit=5,
                min_similarity=0.1
            )
            
            # Fact A should win (higher vector similarity)
            assert "ფაქტი A" in results[0]["fact"], \
                f"Without query_text, vector-only ranking should apply"


# =============================================================================
# DEDUPLICATION TESTS (Existing behavior preserved)
# =============================================================================

class TestDeduplicationPreserved:
    """Ensure deduplication still works with tiered system."""
    
    @pytest.mark.asyncio
    async def test_deduplication_works_across_tiers(self):
        """Duplicate check should look at ALL tiers, not just target tier."""
        from app.memory.mongo_store import UserStore
        
        store = UserStore()
        mock_collection = AsyncMock()
        
        existing_embedding = [1.0] + [0.0] * 767
        
        # User with fact in curated_facts
        user_with_curated = {
            "user_id": "user123",
            "curated_facts": [
                {
                    "fact": "ალერგია მაქვს რძეზე",
                    "embedding": existing_embedding,
                    "importance_score": 0.9,
                    "is_sensitive": True,
                    "created_at": datetime.utcnow()
                }
            ],
            "daily_facts": [],
            "user_facts": []
        }
        mock_collection.find_one = AsyncMock(return_value=user_with_curated)
        
        with patch.object(type(store), 'collection', new=mock_collection):
            # Try to add similar fact (should be duplicate)
            similar_embedding = [0.99] + [0.01] + [0.0] * 766
            result = await store.add_user_fact(
                user_id="user123",
                fact="რძეზე ალერგია მაქვს",  # Same meaning
                embedding=similar_embedding,
                importance_score=0.5  # Different tier target
            )
            
            assert result["status"] == "duplicate", \
                f"Should detect duplicate across tiers, got: {result}"


# =============================================================================
# MEMORY FLUSH TESTS (Phase 3)
# =============================================================================

class TestMemoryFlush:
    """Tests for memory flush before pruning."""
    
    @pytest.mark.asyncio
    async def test_pruning_triggers_flush_memories(self):
        """When history exceeds max_messages, _flush_memories should be called."""
        from app.memory.mongo_store import ConversationStore
        
        # Create store with low max to trigger pruning
        store = ConversationStore(max_messages=5)
        
        # Create mock for _flush_memories
        store._flush_memories = AsyncMock(return_value=None)
        
        # Build history that exceeds max (6 messages > 5)
        large_history = []
        for i in range(35):  # 35 messages, exceeds keep_count=30
            large_history.append({
                "role": "user" if i % 2 == 0 else "model",
                "parts": [{"text": f"Message {i}"}]
            })
        
        # Call _prune_history
        # FIX #3: Now requires user_id parameter
        result_history, summary = await store._prune_history(large_history, user_id="test_user")
        
        # Verify _flush_memories was called with old messages
        # Note: With eager extraction, _flush_memories is called twice:
        # 1. Eager extraction for messages > 10 threshold
        # 2. Pruning extraction for messages > 30 keep_count
        assert store._flush_memories.called, "_flush_memories should be called"
        
        # Get the pruning call (second call with old messages)
        calls = store._flush_memories.call_args_list
        last_call_args = calls[-1][0]  # Last call positional args
        old_messages = last_call_args[0]  # First positional arg
        
        # Should have passed the 5 oldest messages (35 - 30 = 5)
        assert len(old_messages) == 5, f"Expected 5 old messages, got {len(old_messages)}"
    
    @pytest.mark.asyncio
    async def test_flush_extracts_facts_from_messages(self):
        """_flush_memories should extract and save facts from old messages via FactExtractor."""
        from app.memory.mongo_store import ConversationStore
        
        store = ConversationStore()
        
        # Mock the UserStore
        mock_user_store = AsyncMock()
        mock_user_store.add_user_fact = AsyncMock(return_value={"status": "added"})
        store._user_store = mock_user_store
        
        # Messages to extract from
        messages = [
            {"role": "user", "parts": [{"text": "ალერგია მაქვს თხილზე"}]},
            {"role": "model", "parts": [{"text": "გასაგებია, გავითვალისწინებ"}]},
        ]
        
        # Mock the FactExtractor at the module where it's imported inside the function
        with patch('app.memory.fact_extractor.FactExtractor') as MockFactExtractor:
            mock_extractor = MagicMock()
            mock_extractor.extract_facts = AsyncMock(return_value=[
                {"fact": "მომხმარებელს ალერგია აქვს თხილზე", "importance": 0.9, "category": "allergy"}
            ])
            MockFactExtractor.return_value = mock_extractor
            
            # Mock embedding adapter at its source module
            with patch('app.adapters.gemini_adapter.create_gemini_adapter') as MockAdapterFactory:
                mock_adapter = MagicMock()
                mock_adapter.embed_content = AsyncMock(return_value=[0.5] * 768)
                MockAdapterFactory.return_value = mock_adapter
                
                await store._flush_memories(messages, user_id="test_user")
        
        # Should have called add_user_fact with the extracted fact
        assert mock_user_store.add_user_fact.called, "Should save extracted facts"
    
    @pytest.mark.asyncio
    async def test_no_pruning_no_flush(self):
        """When history is within limits, no flush should occur."""
        from app.memory.mongo_store import ConversationStore
        
        store = ConversationStore(max_messages=100)
        store._flush_memories = AsyncMock(return_value=None)
        
        # Small history (under keep_count of 30)
        small_history = [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "model", "parts": [{"text": "Hi there!"}]}
        ]
        
        # FIX #3: Now requires user_id parameter
        result_history, summary = await store._prune_history(small_history, user_id="test_user")
        
        # With eager extraction threshold = 10, small history (2 messages) should NOT trigger flush
        # Should NOT call _flush_memories because 2 < 10 threshold
        store._flush_memories.assert_not_called()
        
        # History should be unchanged
        assert len(result_history) == 2



# =============================================================================
# PHASE 4: FACT EXTRACTION TESTS
# =============================================================================

class TestFactExtractor:
    """Tests for AI-powered fact extraction service."""
    
    @pytest.mark.asyncio
    async def test_fact_extractor_parses_valid_json(self):
        """FactExtractor should parse valid JSON response from Gemini."""
        from app.memory.fact_extractor import FactExtractor
        
        # Mock Gemini response - _parse_response accesses response.text directly
        mock_response = MagicMock()
        mock_response.text = '[{"fact": "მომხმარებელს ალერგია აქვს ლაქტოზაზე", "importance": 0.9, "category": "allergy"}]'
        
        extractor = FactExtractor()
        
        # Mock the client's generate_content method
        with patch.object(extractor, 'client') as mock_client:
            mock_client.models.generate_content = MagicMock(return_value=mock_response)
            
            # Call with async wrapper
            messages = [
                {"role": "user", "parts": [{"text": "ალერგია მაქვს ლაქტოზაზე"}]},
                {"role": "model", "parts": [{"text": "გასაგებია, გავითვალისწინებ"}]}
            ]
            
            facts = await extractor.extract_facts(messages)
            
            assert len(facts) == 1
            assert "ლაქტოზ" in facts[0]["fact"]
            assert facts[0]["importance"] == 0.9
            assert facts[0]["category"] == "allergy"
    
    @pytest.mark.asyncio
    async def test_fact_extractor_handles_empty_response(self):
        """FactExtractor should handle empty/invalid Gemini responses gracefully."""
        from app.memory.fact_extractor import FactExtractor
        
        mock_response = MagicMock()
        mock_response.candidates = []
        
        extractor = FactExtractor()
        
        with patch.object(extractor, 'client') as mock_client:
            mock_client.models.generate_content = MagicMock(return_value=mock_response)
            
            messages = [{"role": "user", "parts": [{"text": "გამარჯობა"}]}]
            facts = await extractor.extract_facts(messages)
            
            assert facts == []  # Should return empty list, not crash
    
    @pytest.mark.asyncio
    async def test_fact_extractor_messages_to_text(self):
        """_messages_to_text should format conversation correctly."""
        from app.memory.fact_extractor import FactExtractor
        
        extractor = FactExtractor()
        
        messages = [
            {"role": "user", "parts": [{"text": "გამარჯობა, მე ვზომავ 180სმ"}]},
            {"role": "model", "parts": [{"text": "მშვენიერია!"}]}
        ]
        
        text = extractor._messages_to_text(messages, max_chars=1000)
        
        assert "გამარჯობა" in text
        assert "180" in text


# =============================================================================
# PHASE 4: CONTEXT INJECTION TESTS
# =============================================================================

class TestContextInjection:
    """Tests for user facts injection into system prompt."""
    
    def test_format_user_facts_with_curated_facts(self):
        """_format_user_facts should prioritize curated facts."""
        from app.core.types import RequestContext
        
        # Create a minimal mock engine with just the method we need
        class MockEngine:
            def _format_user_facts(self, context):
                # Import the actual implementation
                from app.core.engine import ConversationEngine
                # Call the static-like method without full init
                return ConversationEngine._format_user_facts(self, context)
        
        engine = MockEngine()
        
        context = RequestContext(
            user_id="user123",
            message="test"
        )
        context.user_profile = {
            "curated_facts": [
                {"fact": "ალერგია ლაქტოზაზე"},
                {"fact": "მიზანი: კუნთების ზრდა"}
            ],
            "daily_facts": [
                {"fact": "კრეატინი შეიძინა"}
            ]
        }
        
        result = engine._format_user_facts(context)
        
        assert "ალერგია ლაქტოზაზე" in result
        assert "კუნთების ზრდა" in result
        assert "კრეატინი შეიძინა" in result
    
    def test_format_user_facts_empty_profile(self):
        """_format_user_facts should handle empty profile gracefully."""
        from app.core.types import RequestContext
        
        class MockEngine:
            def _format_user_facts(self, context):
                from app.core.engine import ConversationEngine
                return ConversationEngine._format_user_facts(self, context)
        
        engine = MockEngine()
        
        context = RequestContext(user_id="user123", message="test")
        context.user_profile = {}
        
        result = engine._format_user_facts(context)
        
        assert "არ არის შენახული" in result  # Georgian "not saved" message
    
    def test_build_system_instruction_replaces_placeholder(self):
        """_build_system_instruction should replace {{USER_FACTS}} placeholder."""
        from app.core.types import RequestContext
        
        class MockEngine:
            def __init__(self):
                self.system_instruction = "მომხმარებლის ფაქტები:\n{{USER_FACTS}}\nდასასრული"
            
            def _format_user_facts(self, context):
                from app.core.engine import ConversationEngine
                return ConversationEngine._format_user_facts(self, context)
            
            def _format_profile_context(self, profile):
                from app.core.engine import ConversationEngine
                return ConversationEngine._format_profile_context(self, profile)
            
            def _build_system_instruction(self, context):
                from app.core.engine import ConversationEngine
                return ConversationEngine._build_system_instruction(self, context)
        
        engine = MockEngine()
        
        context = RequestContext(user_id="user123", message="test")
        context.user_profile = {
            "curated_facts": [{"fact": "გლუტენზე ალერგია"}]
        }
        
        result = engine._build_system_instruction(context)
        
        # Placeholder should be replaced
        assert "{{USER_FACTS}}" not in result
        assert "გლუტენზე ალერგია" in result


# =============================================================================
# PHASE 2: RESILIENCE TESTS
# =============================================================================

class TestFactExtractorRetry:
    """Tests for FactExtractor retry logic (Phase 2 - Task 1)."""
    
    @pytest.mark.asyncio
    async def test_fact_extractor_retries_on_429_error(self):
        """FactExtractor should retry on rate limit (429) errors."""
        from app.memory.fact_extractor import FactExtractor
        import asyncio
        
        # Track call count
        call_count = 0
        
        # Mock that fails twice then succeeds
        mock_response = MagicMock()
        mock_response.text = '[{"fact": "test fact", "importance": 0.7, "category": "preference"}]'
        
        def mock_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("429 Resource Exhausted: rate limit exceeded")
            return mock_response
        
        extractor = FactExtractor()
        
        with patch.object(extractor, 'client') as mock_client:
            mock_client.models.generate_content = mock_generate
            
            messages = [
                {"role": "user", "parts": [{"text": "test message for retry"}]},
                {"role": "model", "parts": [{"text": "response here"}]}
            ]
            
            # Use short delays for testing
            facts = await extractor.extract_facts(messages, base_delay=0.01)
            
            # Should have retried and succeeded
            assert call_count == 3, f"Expected 3 calls (2 failures + 1 success), got {call_count}"
            assert len(facts) == 1
            assert facts[0]["fact"] == "test fact"
    
    @pytest.mark.asyncio
    async def test_fact_extractor_returns_empty_on_max_retries(self):
        """FactExtractor should return empty list after max retries exhausted."""
        from app.memory.fact_extractor import FactExtractor
        
        call_count = 0
        
        def mock_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("503 Service Unavailable")
        
        extractor = FactExtractor()
        
        with patch.object(extractor, 'client') as mock_client:
            mock_client.models.generate_content = mock_generate
            
            messages = [
                {"role": "user", "parts": [{"text": "test message for retry test"}]},
                {"role": "model", "parts": [{"text": "response with enough text"}]}
            ]
            
            facts = await extractor.extract_facts(messages, max_retries=3, base_delay=0.01)
            
            # Should have tried 3 times (max_retries) and returned empty
            assert call_count == 3, f"Expected 3 calls, got {call_count}"
            assert facts == []
    
    @pytest.mark.asyncio
    async def test_json_parsing_with_regex_fallback(self):
        """_parse_response should extract JSON using regex fallback."""
        from app.memory.fact_extractor import FactExtractor
        
        extractor = FactExtractor()
        
        # Response with text before/after JSON (common LLM output)
        mock_response = MagicMock()
        mock_response.text = '''Here are the facts I extracted:
[{"fact": "has allergy", "importance": 0.8, "category": "health"}]
Let me know if you need more!'''
        
        facts = extractor._parse_response(mock_response)
        
        assert len(facts) == 1
        assert facts[0]["fact"] == "has allergy"
    
    @pytest.mark.asyncio
    async def test_json_parsing_with_trailing_comma(self):
        """_parse_response should handle trailing commas in JSON."""
        from app.memory.fact_extractor import FactExtractor
        
        extractor = FactExtractor()
        
        # JSON with trailing comma (common LLM mistake)
        mock_response = MagicMock()
        mock_response.text = '[{"fact": "test", "importance": 0.5, "category": "preference"},]'
        
        facts = extractor._parse_response(mock_response)
        
        # Should parse successfully after cleaning trailing comma
        assert len(facts) == 1


class TestTTLCleanup:
    """Tests for daily_facts TTL cleanup (Phase 2 - Task 3)."""
    
    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_facts(self):
        """cleanup_expired_daily_facts should remove expired entries."""
        from app.memory.mongo_store import DatabaseManager
        from datetime import datetime
        
        manager = DatabaseManager()
        
        # Mock database with expired facts
        mock_result = MagicMock()
        mock_result.modified_count = 3
        
        mock_db = MagicMock()
        mock_db.users.update_many = AsyncMock(return_value=mock_result)
        
        manager._db = mock_db
        
        removed_count = await manager.cleanup_expired_daily_facts()
        
        # Verify cleanup was called
        assert removed_count == 3
        mock_db.users.update_many.assert_called_once()
        
        # Verify correct MongoDB query
        call_args = mock_db.users.update_many.call_args[0]
        assert "$lt" in str(call_args[0])  # Filter has $lt
        assert "$pull" in call_args[1]  # Update has $pull
    
    @pytest.mark.asyncio
    async def test_cleanup_handles_no_expired_facts(self):
        """cleanup_expired_daily_facts should handle no expired facts gracefully."""
        from app.memory.mongo_store import DatabaseManager
        
        manager = DatabaseManager()
        
        mock_result = MagicMock()
        mock_result.modified_count = 0
        
        mock_db = MagicMock()
        mock_db.users.update_many = AsyncMock(return_value=mock_result)
        
        manager._db = mock_db
        
        removed_count = await manager.cleanup_expired_daily_facts()
        
        assert removed_count == 0


class TestEmbeddingRetry:
    """Tests for embedding generation retry logic (Phase 2 - Task 2)."""
    
    @pytest.mark.asyncio
    async def test_embedding_retry_succeeds_on_third_attempt(self):
        """_flush_memories should retry embedding generation."""
        from app.memory.mongo_store import ConversationStore
        
        store = ConversationStore()
        
        # Mock user_store via patching
        mock_user_store = MagicMock()
        mock_user_store.add_user_fact = AsyncMock(return_value={"status": "added"})
        
        # Track embed calls
        embed_call_count = 0
        
        async def mock_embed(text):
            nonlocal embed_call_count
            embed_call_count += 1
            if embed_call_count < 3:
                raise Exception("Timeout")
            return [0.1] * 768
        
        mock_adapter = MagicMock()
        mock_adapter.embed_content = mock_embed
        
        mock_extractor = MagicMock()
        mock_extractor.extract_facts = AsyncMock(return_value=[
            {"fact": "test fact", "importance": 0.7, "category": "preference"}
        ])
        
        # Patch the module from where it's imported in _flush_memories
        with patch('app.memory.fact_extractor.FactExtractor', return_value=mock_extractor):
            with patch('app.adapters.gemini_adapter.create_gemini_adapter', return_value=mock_adapter):
                with patch.object(store, '_user_store', mock_user_store):
                    messages = [{"role": "user", "parts": [{"text": "test"}]}]
                    await store._flush_memories(messages, user_id="test_user")
        
        # Should have retried embeddings
        assert embed_call_count == 3
        mock_user_store.add_user_fact.assert_called_once()


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

