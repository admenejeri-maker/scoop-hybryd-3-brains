# Changelog - Memory Optimization 2026

All notable changes to the memory system will be documented here.

---

## [v2.1.4] - 2026-01-29 - Text Truncation Bug #28 Fix (Phase 1)

### Fixed

#### 🔧 SAFETY Fallback Threshold (engine.py)
- **Issue**: Georgian health responses truncated when `finish_reason=SAFETY`
- **Root Cause**: 300-char threshold too low for longer Georgian content
- **Fix**: Raised `SAFETY` fallback threshold from 300 → 800 characters

#### 🔄 Sync Path `finish_reason` Parity (function_loop.py)
- **Issue**: `/chat` endpoint didn't capture `finish_reason`
- **Fix**: Added `finish_reason` capture and return in sync path

### Testing

- `TestSafetyFallbackIntegration`: 4 new integration tests
- All 167 core tests passing ✓
- Semgrep security scan: 0 findings
- Manual testing: 11/12 queries successful (92%)

---

## [v2.1.3] - 2026-01-28 - Embedding SDK Migration

### Fixed

#### 🔄 Embedding API Migration (google.genai v1.x)
- **Issue**: `genai.embed_content` deprecated in new SDK
- **Error**: `module 'google.genai' has no attribute 'embed_content'`
- **Fix**: Migrated to `client.models.embed_content()` pattern

#### 📍 Call Sites Updated
| File | Line | Change |
|------|------|--------|
| `gemini_adapter.py` | 607 | `self.client.models.embed_content()` |
| `user_tools.py` | 304, 459 | New `_get_embedding()` helper |

#### 📐 Dimension Validation
- **Issue**: Hardcoded 768-dim checks rejected 3072-dim from new model
- **Model**: `gemini-embedding-001` → 3072 dimensions
- **Fix**: Updated validation to accept both `768` and `3072`

| File | Line | Before | After |
|------|------|--------|-------|
| `engine.py` | 1406 | `== 768` | `in (768, 3072)` |
| `mongo_store.py` | 1123 | `!= 768` | `not in (768, 3072)` |

### Changed

#### Embedding Model
- **Config**: `config.py` → `models/gemini-embedding-001`
- **Dimension**: 768 → 3072 (higher quality vectors)

### Verified
- Facts saved to MongoDB ✓
- 3072-dim embeddings stored ✓
- Semantic deduplication working ✓

---

## [v2.1.2] - 2026-01-28 - Resilience & Scheduler Update

### Added

#### 🕐 TTL Cleanup Scheduler (Phase 3)
- **New Module**: `app/core/scheduler.py`
  - `ScoopScheduler` class using APScheduler
  - Daily cleanup job at 04:00 UTC
  - Removes expired `daily_facts` from user profiles
- **Dependency**: `APScheduler==3.10.4`
- **Integration**: FastAPI lifespan startup/shutdown hooks

#### 🔄 FactExtractor Retry Logic (Phase 2)
- 3-attempt retry with exponential backoff (1s base × 2^attempt)
- Handles transient errors: 429, 503, 500, ResourceExhausted
- Graceful fallback to empty list on persistent failure

#### 🧹 JSON Parsing Robustness (Phase 2)
- Multi-stage parsing: markdown → direct → regex fallback
- Handles trailing commas in JSON arrays
- Extracts valid JSON from mixed text responses

### Changed

#### Embedding Retry Loop
- 3 attempts per fact embedding
- **Breaking**: Now skips facts instead of zero-vector fallback
- Zero-vectors made facts unretrievable via cosine similarity

#### Eager Extraction Threshold
- Changed: 30 messages → 10 messages
- Captures facts sooner, prevents loss in short sessions

### Fixed

#### User ID Data Corruption
- **Issue**: All facts stored under `"current_user"` instead of actual user_id
- **Location**: `mongo_store.py:_flush_memories()`
- **Fix**: Pass `user_id` explicitly through call chain

#### Session End Fact Extraction
- **Issue**: Facts only extracted during pruning (30+ messages)
- **Fix**: Added `_extract_facts_on_session_end()` hook in engine.py
- **Impact**: Every session now captures user facts

### Testing
- 7 new scheduler tests ✓
- 7 new resilience tests ✓
- All 51 tests passing ✓

---

## [v2.1.1] - 2026-01-28 - Incomplete Response Hotfix

### Fixed

#### 🔄 INCOMPLETE_RESPONSE Detection
- **Issue**: Model (`gemini-3-flash-preview`) sometimes stops with `FinishReason.STOP` mid-sentence
- **Symptom**: Responses ending in `:` without completing the list (e.g., "საუკეთესო ვარიანტებია:")
- **Solution**: Added `analyze_text_completeness()` detection in `FallbackTrigger`

#### 🛡️ Automatic Fallback to Stronger Model
- **Trigger**: Response ends with `:` and length > 50 characters
- **Action**: Automatically retry with Tier 2/3 fallback model (gemini-2.5-pro)
- **Location**: `engine.py` - INCOMPLETE check added after existing SAFETY fallback

### Technical Details

**New Files/Methods**:
- `FallbackReason.INCOMPLETE_RESPONSE` enum value
- `FallbackTrigger.analyze_text_completeness()` method
- 5 detection patterns: `:`, `ვარიანტებია:`, `შემდეგია:`, `და`, `მაგრამ`

**Metrics**:
- New counter: `incomplete_responses` in `FallbackTrigger._metrics`

### Testing
- All 60 existing tests passing ✓
- Manual verification of pattern detection ✓
- Security review: No ReDoS risk in regex patterns ✓

---

## [v2.1.0] - 2026-01-28 - The Memory Update

### Added

#### 🧠 AI-Driven Fact Extraction
- **New Module**: `app/memory/fact_extractor.py`
  - `FactExtractor` class using Gemini 2.0 Flash for semantic analysis
  - Georgian-aware prompt for structured extraction: `{fact, importance, category}`
  - Categories: `preference`, `health`, `allergy`, `goal`, `behavior`

#### 🗂️ Tiered Memory Storage
- **Curated Facts** (importance ≥ 0.8): Permanent storage for health/allergy data
- **Daily Facts** (importance < 0.8): 60-day TTL for preferences
- **Schema Update**: `user_profiles` now includes `curated_facts[]` and `daily_facts[]`

#### 🔍 Hybrid Search (Vector + BM25-lite)
- Scoring formula: `0.7 × Vector + 0.3 × Keyword`
- Improves exact brand name matching while preserving semantic search
- Applied to both product search and user fact retrieval

#### 💉 Context Injection
- `{{USER_FACTS}}` placeholder in system prompt
- `engine._format_user_facts()` prioritizes: curated → daily → legacy
- Facts injected per-request for personalized responses

### Changed

#### Memory Flush Hook
- `ConversationStore._flush_memories()` now uses `FactExtractor` instead of `FACT:` heuristic
- Automatic embedding generation for extracted facts
- Health/allergy categories auto-boosted to curated tier

### Testing

#### Full Regression Suite — 318 Tests Passing ✓
- Core Engine: 40 integration tests
- Function Loop: 42 tests
- Response Buffer: 41 tests
- Thinking Manager: 28 tests
- Search-First: 16 tests
- Bug Fixes: 10 tests
- Tiered Memory: 19 tests (extraction, routing, hybrid search)
- Token Counter: 15 tests
- All E2E pipelines verified

---

## [Week 1] - 2026-01-13

### Fixed

#### 🐛 Summary Injection Bug
- **Issue**: Summary was logged but never passed to Gemini model
- **Location**: `main.py:292-294`
- **Fix**: Prepend summary as context message in history
- **Impact**: AI can now actually recall summarized conversations

**Before**:
```python
if summary and not history:
    logger.info(f"Injecting summary...")  # Does nothing!
```

**After**:
```python
if summary:
    summary_message = {
        "role": "user",
        "parts": [{"text": f"[წინა საუბრის კონტექსტი: {summary}]"}]
    }
    gemini_history = [summary_message] + gemini_history
```

### Changed

#### 📦 Summary Retention Extended
- **Schema Update**: Added `summary_expires_at` field
- **TTL Change**: 7 days → 30 days for summaries
- **Rationale**: Summaries are cheap (~500 tokens) vs raw history (~20k tokens)
- **Files Modified**:
  - `app/memory/mongo_store.py` (schema, indexes)
  - `scripts/migrate_summary_ttl.py` (new migration script)

### Added

#### 📄 New Files
- `CHANGELOG.md` - This file
- `scripts/migrate_summary_ttl.py` - Database migration for TTL update
- `docs/WEEK_1_VERIFICATION.md` - Testing procedures

---

## [Week 4] - 2026-01-13

### Added

#### Context Caching for 85% Token Savings
- **New Module**: `app/cache/context_cache.py`
  - `ContextCacheManager` - Manages Gemini cached content
  - `CacheRefreshTask` - Background task for auto-refresh
  - `CacheMetrics` - Tracks hits, misses, and cost savings

#### New API Endpoints
- `GET /cache/metrics` - View cache statistics (admin)
- `POST /cache/refresh` - Manual cache refresh (admin)
- Updated `/health` to include cache status

#### Configuration Options
```bash
ENABLE_CONTEXT_CACHING=true
CONTEXT_CACHE_TTL_MINUTES=60
CACHE_REFRESH_BEFORE_EXPIRY_MINUTES=10
CACHE_CHECK_INTERVAL_MINUTES=5
```

### Changed

#### SessionManager Updated
- **Location**: `main.py:241-330`
- Now accepts optional `cache_manager` parameter
- Uses `cached_content` when cache is valid
- Falls back to full system instruction if cache unavailable

#### CatalogLoader Enhanced
- **Location**: `app/catalog/loader.py`
- Added `initialize_context_cache()` method
- Added `refresh_context_cache()` method
- Removed deprecated old SDK caching methods

### Technical Details

**Cached Content**:
- System prompt: ~5,000 tokens
- Product catalog: ~60,000 tokens
- Total cached: ~65,000 tokens

**Cost Savings**:
- Before: $0.075/1M input tokens
- After: $0.01875/1M cached tokens (75% discount)
- Estimated monthly savings: $306

### Safety
- Full backward compatibility with Weeks 1-3
- Graceful degradation if cache fails
- No changes to summarization or memory systems

---

## [Week 3] - Completed

### Added
- `ConversationSummarizer` class for LLM-based summaries
- Semantic understanding replaces keyword extraction
- Fallback to simple summary if LLM fails

---

## [Week 2] - Completed

### Changed
- Migrated from `google.generativeai` to `google.genai` SDK
- Updated all imports and model initialization
- Fixed async/sync issues with tool functions

---

---

## Migration Notes

### Week 1 → Production
- Run `python scripts/migrate_summary_ttl.py` before deployment
- No breaking changes, backward compatible
- Monitor MongoDB TTL index creation

---

**Format**: Based on [Keep a Changelog](https://keepachangelog.com/)
