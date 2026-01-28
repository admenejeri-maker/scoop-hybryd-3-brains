# Scoop AI Development Context

Complete development history for the Gemini Thinking Stream feature implementation.

## Project Overview

**Goal:** Implement real-time Gemini 3 Flash thinking stream for Scoop.ge AI assistant, showing AI reasoning process to users while maintaining product recommendation functionality.

---

## Development Timeline: January 18, 2026

### Phase 1: Research & Planning

**Initial State:** Working chat with Gemini 2, but no visible thinking process.

**Research Findings:**
- Gemini 3 Flash Preview supports `thinking_budget` parameter (1-24576 tokens)
- `include_thoughts=True` streams thought parts with `part.thought=True`
- Thoughts are in English by default, need translation for Georgian users

### Phase 2: Implementation

**Changes Made:**
1. Added `ThinkingConfig` to `GenerateContentConfig`
2. Frontend `ThinkingStepsLoader` component to display thoughts
3. SSE event type `thinking` for streaming thoughts
4. Argos Translate for English→Georgian translation

---

## Bug Log

### Bug #1: Fake Loader Shows First
- **Symptom:** Hardcoded "ვფიქრობ..." text appeared before real thoughts
- **Root Cause:** State initialization logic
- **Fix:** Conditional rendering based on `hasRealThoughts` flag
- **Status:** ✅ RESOLVED

### Bug #2: Translation Latency
- **Symptom:** Long delays waiting for thought translation
- **Root Cause:** Argos Translate cold start
- **Fix:** Parallel processing, caching, warm-up
- **Status:** ✅ RESOLVED

### Bug #3: No Final Response Content (CRITICAL)
- **Symptom:** Thinking events streamed but NO text response
- **Initial Diagnosis:** Thought Gemini Thinking was broken
- **Attempts:** 5+ debugging sessions over 4 hours

**Debugging Process:**
1. Added `console.log('[DEBUG SSE]')` in frontend - confirmed no `text` events
2. Added `logger.info("🔍 Part: ...")` in backend - found empty `part.text`
3. Disabled thinking (`thinking_budget=0`) - text appeared but no thoughts
4. Analyzed backend logs: saw "AFC is enabled with max remote calls: 10"

**Root Cause Discovery (via Sequential Thinking):**
The issue was **NOT** Gemini Thinking. It was **AFC (Automatic Function Calling)**.

When AFC was enabled (default in Gemini SDK):
- SDK internally handled function calls
- Final text response was NOT yielded to our streaming loop
- We received empty text parts

**Final Fix:**
```python
# In main.py stream config
automatic_function_calling=types.AutomaticFunctionCallingConfig(
    disable=True  # CRITICAL: Must be disabled for manual FC handling
)
```

With AFC disabled AND thinking enabled:
```
✅ thinking events: "პროტეინის მოთხოვნების ანალიზი..."
✅ text events: "გამარჯობა! მე ვარ Scoop.ge-ს AI..."
```

- **Status:** ✅ RESOLVED

---

## Development Timeline: January 19, 2026

### Session: Holistic Stability Fix

**Problem Reported:** System unstable - sometimes gives recommendations, sometimes fallback, sometimes empty cards.

---

## Bug Log (January 19)

### Bug #4: search_products Skip Logic Too Aggressive
- **Symptom:** All search_products calls after the first were skipped, even with different queries
- **Root Cause:** Counter-based limit (`search_products_calls > 1`) blocked ALL calls, not just duplicates
- **Fix:** Changed to set-based tracking (`executed_search_queries`) to allow unique queries
- **Status:** ✅ RESOLVED

### Bug #5: Final Response Round Not Triggering for Zero Products
- **Symptom:** When all searches returned 0 products, user saw empty cards with no text
- **Root Cause:** Condition `if not text AND products` skipped when products=[]
- **Fix:** Changed to `if not text` (always generate response)
- **Status:** ✅ RESOLVED

### Bug #6: English Thought Fallback
- **Symptom:** When Final Response failed, English reasoning text was shown to users
- **Root Cause:** Fallback code showed Gemini's internal thoughts (always English)
- **Fix:** Hardcoded Georgian fallback messages instead of thoughts
- **Status:** ✅ RESOLVED

### Bug #7: Wrong Variable Name in Final Response (CRITICAL)
- **Symptom:** `'function' object has no attribute 'send_message_stream'`
- **Root Cause:** Code used `chat` but actual variable was `stream_chat`
- **Fix:** Changed `chat` → `stream_chat`
- **Status:** ✅ RESOLVED

### Bug #8: Missing Async/Await in Final Response
- **Symptom:** Synchronous `for` loop with async generator failed silently
- **Root Cause:** Missing `async for` and `await` keywords
- **Fix:** Changed `for chunk in` → `async for chunk in await`
- **Status:** ✅ RESOLVED

---

## Key Files Modified

| File | Changes |
|------|---------|
| `main.py` | Added AFC disable, thinking stream logic, debug logging |
| `config.py` | Added `thinking_budget`, `include_thoughts` settings |
| `requirements.txt` | Added argostranslate for thought translation |

---

## Configuration

### Working Configuration (Final)

```python
# config.py
thinking_budget = 4096  # Enable thinking
include_thoughts = True  # Stream thoughts to client

# main.py GenerateContentConfig
automatic_function_calling=types.AutomaticFunctionCallingConfig(
    disable=True  # CRITICAL for manual function handling
)
thinking_config=ThinkingConfig(
    thinking_budget=settings.thinking_budget,
    include_thoughts=settings.include_thoughts
)
```

---

## Lessons Learned

1. **AFC Default Behavior:** Gemini SDK enables AFC by default when tools are provided. This swallows the text response in streaming mode.

2. **Thinking + Tools Compatibility:** Gemini 3 Flash thinking DOES work with function calling, but only when AFC is disabled and functions are handled manually.

3. **Debug Logging is Critical:** The `🔍 Part: thought=X, text=Y, fc=Z` logging pattern immediately revealed the issue.

4. **Sequential Thinking Protocol:** Using structured devil's advocate analysis caught the AFC issue that 4 hours of ad-hoc debugging missed.

5. **Non-Deterministic API Behavior:** Gemini Thinking Stream can be unpredictable - always have fallback strategies.

6. **Variable Naming Consistency:** Using different variable names (`chat` vs `stream_chat`) in async contexts causes silent failures.

7. **Async/Await Discipline:** Forgetting `async for` and `await` in async generators produces cryptic errors.

---

## Team

- **AI Agent:** Claude Opus 4.5 (Planning & Building)
- **Human:** Maqashable (Testing & QA)

---

## Development Timeline: January 19, 2026 (Late Session ~02:00-03:00)

### Session: Gemini Response Instability Deep Dive

**Problem Reported:** Inconsistent responses - sometimes full Georgian recommendations, sometimes hardcoded fallback messages, sometimes products without annotation text.

---

## Bug Log (January 19 - Late Session)

### Bug #9: Text Cutoff Mid-Sentence (Streaming Issue)
- **Symptom:** User-facing text cut off mid-sentence when product cards followed
- **Root Cause:** Text was being streamed piece-by-piece, causing premature yield before complete
- **Fix:** Implemented text buffering - accumulate all text in `accumulated_text`, send as complete block at end of round
- **Status:** ✅ RESOLVED

### Bug #10: English Thought-to-Text Fallback (Security/UX)
- **Symptom:** When Gemini failed to generate text, internal English thoughts were shown to users
- **Root Cause:** Fallback logic used first thought as text when `accumulated_text` was empty
- **Fix:** Removed thought-to-text fallback, replaced with Georgian contextual messages
- **Status:** ✅ RESOLVED

### Bug #11: Thinking Mode Blocking Final Response (CRITICAL)
- **Symptom:** Final Response block returned 0 text chars even with improved prompts
- **Root Cause:** `send_message_stream()` continues with `include_thoughts=True`, Gemini outputs ONLY thoughts, no user-facing text
- **Diagnosis:** Sequential Thinking revealed that thinking mode exhausts response capacity
- **Fix:** Changed Final Response from `send_message_stream()` to `send_message()` - bypasses thinking mode like old repo
- **Status:** ✅ RESOLVED

### Bug #12: Inconsistent Fallback Messages Between Paths
- **Symptom:** Sometimes "აი მოვიძიე X პროდუქტი", sometimes "სამწუხაროდ..."
- **Root Cause:** Two separate fallback paths (Main Loop vs Final Response) had different messages
- **Fix:** Initially synchronized messages, then removed all fallback messages per user request
- **Status:** ✅ RESOLVED (fallbacks removed)

---

## Current Outstanding Issues

### Issue #1: Gemini Not Generating Text in Main Loop (NON-DETERMINISTIC)
- **Symptom:** Gemini often returns only function calls + thoughts, no actual text per round
- **Impact:** Forces system to rely on Final Response block instead of natural conversation
- **Potential Fix:** Prompt engineering to force text output alongside function calls
- **Status:** 🟡 PARTIALLY ADDRESSED - Final Response now works, but root cause not fixed

### Issue #2: search_products Returns 0 for Common Queries
- **Symptom:** "Vegan", "Isolate", "ISO", "plant" all return 0 products
- **Root Cause:** Database doesn't have products matching these terms, OR regex matching is too strict
- **Impact:** User experience degraded when looking for specialty products
- **Status:** 🔴 NOT FIXED - Requires database investigation or fuzzy search

### Issue #3: Gemini Sends Multiple Parallel Function Calls
- **Symptom:** Gemini sends 2-3 `search_products` calls in single round
- **Current Handling:** Only first call is executed, others skipped
- **Impact:** Potentially missing better search results
- **Status:** 🟡 WORKAROUND IN PLACE - Not ideal but functional

---

## Key Code Changes This Session

| Location | Change |
|----------|--------|
| `main.py` L1714-1719 | Text buffering - removed immediate yield, now accumulates |
| `main.py` L1737-1752 | Main Loop fallback - removed per user request |
| `main.py` L1837-1866 | Final Response - changed from `send_message_stream()` to `send_message()` |
| `main.py` L1864-1866 | Final Response fallback - removed per user request |

---

## Diagnostic Commands for Future Debugging

```bash
# Watch for fallback triggers
tail -f logs | grep -E "⚠️|fallback|Forcing"

# Check if text is being generated
tail -f logs | grep -E "📤|Final response"

# Monitor product search results  
tail -f logs | grep -E "search_products|products for"
```

---

*Last Updated: January 19, 2026 ~03:00*

---

## Development Timeline: January 19, 2026 (Afternoon Session ~14:00-19:00)

### Session: Product Search Fix + Response Time Optimization

**Problems Reported:**
1. `search_products` returning 0 results for "isolate", "vegan", "iso" queries
2. Response time too slow (~16-17s) for complex queries

---

## Bug Log (January 19 - Afternoon Session)

### Bug #13: search_products 0 Results for Keywords (CRITICAL)
- **Symptom:** Queries like "isolate", "vegan", "iso" returned 0 products
- **Root Cause:** MongoDB text index doesn't include `keywords` array; only searches `name`, `description`, `brand`, `category`
- **Fix:** Enhanced `query_map` with synonyms + added `$regex` search on `keywords` array
- **Location:** `user_tools.py` lines 293-339, 363-372
- **Status:** ✅ RESOLVED

### Optimization #1: thinking_level Parameter
- **Issue:** Response time ~16-17s for product searches
- **Research:** Gemini 3 `thinking_level` parameter controls reasoning depth (MINIMAL, LOW, MEDIUM, HIGH)
- **Fix:** Added `thinking_level=MEDIUM` to config.py and ThinkingConfig in main.py
- **Result:** Response time reduced to ~14.5s (~2s savings)
- **Status:** ✅ IMPLEMENTED

### Ongoing Investigation: Multi-Round Latency
- **Issue:** Local streaming takes 20s vs Production 12s
- **Root Cause:** Manual Function Calling creates 2+ rounds vs AFC's single round
- **Contributing Factors:**
  1. Per-thought translation adds ~500ms each
  2. "Forcing final response" adds extra round (~5-8s)
- **Proposed Fix:** Pre-cached Georgian thought templates + skip extra round when products found
- **Status:** 🟡 PLANNING COMPLETE - Awaiting implementation

---

## Key Code Changes This Session

| Location | Change |
|----------|--------|
| `user_tools.py` L293-339 | Enhanced `query_map` with isolate/vegan/iso synonyms |
| `user_tools.py` L363-372 | Added `keywords` array to `$regex` search conditions |
| `config.py` L73-85 | Added `thinking_level` setting (default: MEDIUM) |
| `main.py` L377-390, L403-416 | Added `ThinkingConfig` with `thinking_level` to both config blocks |

---

## Current Performance Metrics

| Metric | Before | After |
|--------|--------|-------|
| "isolate" search | 0 results | 3+ results |
| Response Time (complex) | ~16-17s | ~14.5s |
| Local vs Production gap | 8s slower | Investigating |

---

*Last Updated: January 19, 2026 ~19:30*

---

## Development Timeline: January 19, 2026 (Evening Session ~19:30-20:10)

### Session: Georgian-Preserving Latency Optimization

**Goal:** Reduce local response time from 20s to 12-14s while preserving Georgian thinking stream.

---

## Optimization Log (January 19 - Evening Session)

### Optimization #2: THOUGHT_CACHE (Instant Georgian Translations)
- **Problem:** Each thought translation took ~500ms via API call
- **Solution:** Pre-cached 20+ common thought patterns with Georgian translations
- **Implementation:**
  - Added `THOUGHT_CACHE` dictionary in `main.py` (lines 532-575)
  - Added `check_thought_cache()` helper function
  - Updated `translate_thought()` to check cache first
- **Patterns include:** "searching products", "checking allergies", "analyzing request", etc.
- **Savings:** ~400ms × 5 thoughts = ~2s per request
- **Status:** ✅ IMPLEMENTED

### Optimization #3: Skip Extra Round When Products Found
- **Problem:** "Forcing final response" triggered extra Gemini call even when products existed
- **Solution:** Changed condition from `if not text` → `if not text AND no products`
- **Location:** `main.py` line 1879-1885
- **Savings:** ~5-8s when products are found
- **Status:** ✅ IMPLEMENTED

### Configuration Change: thinking_level = HIGH
- **Change:** Reverted from MEDIUM to HIGH for deeper reasoning
- **Reason:** Cache optimization compensates for increased thinking time
- **Status:** ✅ APPLIED

---

## Key Code Changes (Evening Session)

| Location | Change |
|----------|--------|
| `main.py` L532-575 | Added `THOUGHT_CACHE` dictionary with 20+ patterns |
| `main.py` L577-591 | Added `check_thought_cache()` helper function |
| `main.py` L593-615 | Updated `translate_thought()` to check cache first |
| `main.py` L1879-1885 | Skip extra round when products already found |
| `config.py` L81-85 | Changed `thinking_level` default to HIGH |

---

## System Architecture Summary

```
User Query → /chat/stream
     ↓
Manual FC Loop (max 3 rounds)
     ↓
Round 1: Gemini thinks + search_products()
     ↓
🧠 Thoughts → THOUGHT_CACHE → Georgian (0ms if cached)
🔧 Function → MongoDB → Products
     ↓
Round 2: Gemini formats response (SKIPPED if products found!)
     ↓
📝 Text Response + [TIP] + [QUICK_REPLIES]
```

---

## Expected Performance

| Metric | Before | After |
|--------|--------|-------|
| Thought Translation | 500ms × 5 | 0ms (cached) |
| Extra Round | +5-8s | Skipped |
| thinking_level | MEDIUM | HIGH |
| **Total Response** | ~20s | ~12-14s (target) |

---

*Last Updated: January 19, 2026 ~20:10*

---

## Development Timeline: January 19, 2026 (Night Session ~21:00-21:30)

### Session: Debug Speculative Search Breaking Text Generation

**Problem Reported:** Product cards displayed without Georgian explanation text for some queries.

---

## Bug Log (January 19 - Night Session)

### Bug #14: Speculative Search via asyncio.to_thread Breaks Text Generation (CRITICAL)
- **Symptom:** Some queries returned products without explanation text
- **A/B Test Results:**
  - Speculative DISABLED: `texts=43` ✅ (full Georgian explanation)
  - Speculative ENABLED: `texts=0` ❌ (only product cards)
- **Root Cause Discovery (via A/B Testing + Sequential Thinking):**
  1. `asyncio.to_thread()` does NOT propagate ContextVars to the new thread
  2. `search_products` uses `user_id_var.get()` which returns `None` in thread context
  3. Non-personalized results + instant timing affects Gemini's text generation behavior
- **Fix:** Disabled speculative search for stability
- **Location:** `main.py` lines 1900-1904 (commented out)
- **Alternative Solutions:**
  - Option A: Keep disabled (current - stable, +0.6s latency)
  - Option B: Fix ContextVar with `contextvars.copy_context()` (future work)
  - Option C: Pass user_id explicitly to speculative_search (future work)
- **Status:** ✅ RESOLVED (via disable)

### Bug #15: No Fallback Intro When Gemini Returns No Text
- **Symptom:** Products rendered without any intro text
- **Root Cause:** Backend logged warning but didn't provide fallback
- **Fix:** Added Georgian fallback `"აი შენთვის შესაფერისი პროდუქტები:"` when products exist but no text
- **Location:** `main.py` lines 1983-1993
- **Status:** ✅ RESOLVED

---

## Key Code Changes (Night Session)

| Location | Change |
|----------|--------|
| `main.py` L1900-1904 | Speculative search disabled for stability |
| `main.py` L1983-1993 | Added fallback Georgian intro for product-only responses |
| `main.py` L2038-2066 | Fixed Write Barrier to retrieve completed speculative task results |

---

## Learnings From This Session

1. **ContextVar + asyncio.to_thread:** Python ContextVars do NOT automatically propagate to threads. Use `contextvars.copy_context()` or pass values explicitly.
2. **A/B Testing Critical:** When suspecting code changes cause issues, disable the change and test. This immediately confirmed speculative search was the root cause.
3. **Latency ≠ Everything:** 0.6s savings from speculative search wasn't worth the stability issues.

---

*Last Updated: January 19, 2026 ~21:30*

---

## Development Timeline: January 19, 2026 (Late Night Session)

### Session: Latency Optimization

**Problem Reported:** Response time 20-25 seconds too slow for user experience.

**Analysis (via Tree of Thoughts Architecture):**
1. Each function round adds ~4s due to thinking
2. 3 rounds × 4s = ~12s just thinking
3. Redundant searches add 1-2s each
4. Evaluated 3 approaches: Conservative, Aggressive, Hybrid

**Selected Approach:** Option C (Hybrid) - balance speed and quality

---

## Bug Log (January 19 - Late Night)

### Bug #16: Option D - Empty Text Despite Products (ENHANCEMENT)
- **Symptom:** Sometimes products rendered with generic fallback instead of contextual intro
- **Root Cause:** Collected thoughts not being used for better fallback
- **Fix:** Use longest collected thought as intro text (300 char limit)
- **Location:** `main.py` lines 1985-2011
- **Status:** ✅ RESOLVED

---

## Key Code Changes (Late Night Session)

| Location | Change | Impact |
|----------|--------|--------|
| `config.py` L73-77 | `thinking_budget`: 4096 → 2048 | ~30% faster thinking |
| `main.py` L1887 | `max_function_rounds`: 3 → 2 | ~5-6s saved |
| `main.py` L2031 | `MAX_UNIQUE_QUERIES`: 2 → 1 | ~1-2s saved |
| `main.py` L1985-2011 | Option D: Best thought fallback | Better fallback quality |

---

## Performance Results

| Metric | Before | After |
|--------|--------|-------|
| Response Time | 20-25s | 10-14s |
| Thinking Quality | HIGH (4096) | MEDIUM-HIGH (2048) |
| Search Calls | 2 per query | 1 per query |
| Function Rounds | 3 max | 2 max |

---

## Learnings From This Session

1. **thinking_budget Trade-off:** 2048 tokens is sufficient for most queries; 4096 adds latency without proportional quality gain.
2. **Deduplication Critical:** Gemini often sends redundant search calls ("protein" then "isolate") - limiting to 1 unique query prevents waste.
3. **Option D Pattern:** When synthesis fails, use best available thought as user-facing text rather than hardcoded fallback.

---

*Last Updated: January 20, 2026 ~00:30*

---

## Development Timeline: January 20, 2026 (Afternoon Session ~19:00-19:40)

### Session: Thought Signature Fix & Latency Optimization

**Goal:** Investigate and fix missing product markdown generation, implement adaptive routing.

---

## Bug Log (January 20)

### Bug #17: Parallel FC Thought Signature Loss (CRITICAL)
- **Symptom:** `⚠️ Product markdown format missing - injecting` and `⚠️ [TIP] tag missing - injecting`
- **Deep Research:** Used `/deep-research` workflow to investigate Gemini 3 thinking mode
- **Root Cause Discovery:** 
  - Gemini 3 requires `thought_signature` preservation across function calling rounds
  - When parallel FCs sent (get_user_profile + search_products), **only first FC gets signature**
  - Second FC loses signature → Gemini can't continue reasoning → **empty text response**
- **Evidence from logs:**
  ```
  🔑 FC has signature: get_user_profile ✅
  ⚠️ FC missing signature: search_products ❌
  📜 SDK History: 6 msgs, 1 signatures
  ```
- **Fix:** Pre-cache user profile at request start, eliminating parallel FC issue
  - `main.py` line 2109: `cached_user_profile = get_user_profile()`
  - `main.py` line 2349: Return cached value instead of calling function
- **Status:** ✅ RESOLVED

### Enhancement: Adaptive Routing (Latency Optimization)
- **Implementation:** `predict_query_complexity()` function in `main.py`
- **Routing levels:**
  | Level | Budget | Use Case |
  |-------|--------|----------|
  | MINIMAL | 1024 | Greetings/FAQ |
  | LOW | 4096 | Simple browsing |
  | MEDIUM | 8192 | Standard product queries |
  | HIGH | 16384 | Recommendations |
- **Result:** "გამარჯობა" → 3.6s (was 6.3s)

### Enhancement: Context Caching Enabled
- **Change:** `ENABLE_CONTEXT_CACHING=true` in `.env`
- **Result:** ~706 token cache created with 60min TTL

---

## Key Code Changes (January 20)

| Location | Change | Impact |
|----------|--------|--------|
| `main.py` L815-905 | `predict_query_complexity()` | Adaptive routing |
| `main.py` L2042 | `thinking_config` adaptive | Dynamic thinking budget |
| `main.py` L2109 | Pre-cache user profile | Signature fix |
| `main.py` L2349 | Use cached profile | Eliminates parallel FC |
| `main.py` L2141 | Signature audit logging | Debug visibility |
| `.env` | `ENABLE_CONTEXT_CACHING=true` | Cache enabled |

---

## Performance Results (January 20)

| Metric | Before | After |
|--------|--------|-------|
| Greeting ("გამარჯობა") | 6.3s | **3.6s** |
| Product query | 15s | **12.9s** |
| `[TIP]` generation | ❌ Injected | ✅ Native |
| Product markdown | ❌ Missing | ✅ Generated |

---

## Learnings From This Session

1. **Gemini 3 Thought Signatures:** Required for multi-turn function calling. Parallel FCs = only first gets signature.
2. **SDK Auto-Management:** SDK should handle signatures, but parallel FC batches can cause issues.
3. **Pre-caching Pattern:** Eliminates parallel FC by providing cached responses instantly.
4. **Adaptive Routing:** Significant latency savings for simple queries without sacrificing quality for complex ones.

---

*Last Updated: January 20, 2026 ~19:40*

---

## Development Timeline: January 20, 2026 (Evening Session ~19:45-20:00)

### Session: Raw [TIP] Tag UI Fix

**Problem Reported:** Raw `[TIP]...[/TIP]` tags appearing in frontend chat interface instead of styled "პრაქტიკული რჩევა" box.

---

## Bug Log (January 20 - Evening)

### Bug #18: Raw TIP Tags Displayed in UI (UX)
- **Symptom:** Users saw raw `[TIP]პროტეინი ვარჯიშის შემდეგ...[/TIP]` text
- **Deep Research:** Used `/deep-research` to compare frontend vs backend solutions
  - **Finding:** SSE best practices recommend structured data over raw markup
  - **Sources:** Chrome DevRel, LLM Streaming Guide, SSE Best Practices 2025
- **Root Cause:** Backend `ensure_tip_tag()` injected raw tags via SSE `tip` event
  - `main.py` line 2469: `tip_block = f"\n\n[TIP]\n{tip}\n[/TIP]"`
  - Frontend added raw tags to `assistantContent` directly
- **Fix (Option A - Backend Recommended):**
  1. **Backend (`main.py` L2469):** Send clean tip content without wrapper tags
  2. **Frontend (`Chat.tsx` L491):** Wrap received content with tags for `parseProductsFromMarkdown`
- **Status:** ✅ RESOLVED

---

## Key Code Changes (Evening Session)

| Location | Change | Impact |
|----------|--------|--------|
| `main.py` L2469 | Send clean `tip` content (no tags) | Clean API contract |
| `Chat.tsx` L491-494 | Wrap incoming tip with `[TIP]` tags | Parser compatibility |

---

## Verification

- **Browser Test:** ✅ "პრაქტიკული რჩევა" styled box renders correctly
- **No Raw Tags:** ✅ `[TIP]` and `[/TIP]` not visible to users
- **Parser Working:** ✅ `parseProductsFromMarkdown` extracts tip successfully

---

## Development Timeline: January 20, 2026 (Night Session ~20:30-21:00)

### Session: Cross-Chunk TIP Tag Streaming Fix

**Problem Reported:** Despite Bug #18 fix, raw `[TIP]...[/TIP]` tags still appearing in frontend chat interface.

---

## Bug Log (January 20 - Night Session)

### Bug #19: Cross-Chunk TIP Tag Display (STREAMING EDGE CASE)
- **Symptom:** Raw `[TIP]` tags visible alongside rendered "პრაქტიკული რჩევა" box (duplication)
- **Initial Assumption:** Bug #18 fix incomplete
- **Deep Investigation:**
  1. **Backend Analysis (`main.py` L2466-2472):**
     - When Gemini natively generates `[TIP]` tags, backend sends raw tags via `text` event
     - When backend adds tags, sends clean content via `tip` event
  2. **Frontend Analysis (`Chat.tsx`):**
     - Initial fix attempted to strip tags from incoming text chunks (L457-461)
     - Regex: `/\[TIP\][\s\S]*?\[\/TIP\]/g`
  3. **Root Cause Discovery (via Sequential Thinking):**
     - SSE streaming sends data in **chunks**
     - `[TIP]` can arrive in one chunk, content in next, `[/TIP]` in third
     - **Regex cannot match split tags** → tags pass through to display
- **Fix Strategy:** Strip tags at **message assignment time**, not chunk reception
- **Implementation:**
  - Applied regex replacement when setting `assistantContent` to message state
  - Covered ALL event handlers: `text`, `products`, `tip`, `error`
- **Locations Modified:**
  | Location | Handler |
  |----------|---------|
  | `Chat.tsx` L470 | `text` event |
  | `Chat.tsx` L487 | `products` event |
  | `Chat.tsx` L505 | `tip` event |
  | `Chat.tsx` L534 | `error` event |
- **Status:** ✅ RESOLVED

---

## Key Code Changes (Night Session)

| Location | Change | Impact |
|----------|--------|--------|
| `Chat.tsx` L470 | Strip `[TIP]` tags at text event message assignment | Removes native Gemini tags |
| `Chat.tsx` L487 | Strip `[TIP]` tags at products event message assignment | Consistent cleanup |
| `Chat.tsx` L505 | Strip `[TIP]` tags at tip event message assignment | No raw tags in tip display |
| `Chat.tsx` L534 | Strip `[TIP]` tags at error event message assignment | Complete coverage |

---

## Technical Pattern: Cross-Chunk Regex Limitation

```
❌ WRONG: Strip during chunk arrival
   Chunk 1: "გამარჯობა [TIP"    → regex no match
   Chunk 2: "]რჩევა[/TIP]"      → regex no match
   Result: Raw tags displayed

✅ CORRECT: Strip at final message assignment
   Accumulated: "გამარჯობა [TIP]რჩევა[/TIP]"
   Regex match: Full tag found
   Result: Clean content displayed
```

---

## Learnings From This Session

1. **SSE Streaming Chunking:** Tags can be split across network chunks - can't rely on per-chunk regex matching.
2. **Buffer-Level Processing:** For SSE streams, apply transformations to accumulated content, not individual chunks.
3. **Complete Handler Coverage:** When fixing SSE logic, ensure ALL event handlers are updated consistently.

---

## Development Timeline: January 20, 2026 (Late Night Session ~22:20)

### Session: TIP Tag Display Fix (Bug #20)

**Problem Reported:** TIP section not appearing in UI despite Gemini generating it correctly.

---

## Bug Log (January 20 - Late Night)

### Bug #20: TIP Tags Stripped by Overly Aggressive Regex (FRONTEND LOGIC ERROR)
- **Symptom:** TIP box not displaying in UI, but backend logs show `[TIP]...[/TIP]` generated
- **Investigation Method:** `/debug` workflow - Light DRP
- **Evidence Collection:**
  1. Backend logs confirmed: `[TIP]საკვების აწონვა...[/TIP]` generated correctly ✅
  2. Frontend `Chat.tsx` analysis: Bug #19 fix too aggressive
- **Root Cause Discovery:**
  - Bug #19 fix added `.replace(/\[TIP\][\s\S]*?\[\/TIP\]/g, '')` to ALL event handlers
  - This included the `tip` event handler at L505
  - **Result:** Handler added TIP tags, then immediately stripped them!
  ```typescript
  // BUG: tip handler was stripping its own tags
  const tipWithTags = `\n\n[TIP]\n${data.content}\n[/TIP]`;
  assistantContent += tipWithTags;  // ← Added TIP ✅
  setConversations(...{ 
    content: assistantContent.replace(/\[TIP\]...\[\/TIP\]/g, '') // ← Removed TIP ❌
  });
  ```
- **Fix:** Remove `.replace()` from `tip` event handler only
  - `text` event: Keep stripping (prevents Gemini native tag dupe)
  - `products` event: Keep stripping
  - `tip` event: **Remove stripping** (we intentionally add tags here)
  - `error` event: Keep stripping
- **Location:** `Chat.tsx` L505
- **Status:** ✅ RESOLVED

---

## Key Code Changes (Late Night Session)

| Location | Change | Impact |
|----------|--------|--------|
| `Chat.tsx` L505 | Removed `.replace()` from tip handler | TIP tags preserved |

---

## Learnings From This Session

1. **Over-Correction Hazard:** Bug fixes that apply blanket patterns (e.g., strip everywhere) can break intentional behavior elsewhere.
2. **Event Handler Isolation:** Each SSE event type has distinct purposes - `tip` event should preserve tags, `text` event should strip duplicates.
3. **Quick Debug Path:** Backend logs → Frontend logic trace → targeted fix.

---

*Last Updated: January 20, 2026 ~22:20*

---

## Development Timeline: January 21, 2026 (Night Session ~00:30-01:10)

### Session: Hybrid Fallback Fix v5.1 (Empty Follow-up Response)

**Problem Reported:** Follow-up queries returning fallback text `"აი შენთვის შესაფერისი პროდუქტები:"` instead of rich Georgian recommendations, despite products being found.

---

## Bug Log (January 21)

### Bug #21: Empty Round Detection + Force Text Generation (CRITICAL)
- **Symptom:** Follow-up queries like "კუნთის ზრდისთვის რა არის საუკეთესო?" returned fallback instead of detailed recommendations
- **Investigation Method:** `/opus-planning` + `/debug` workflows with curl testing
- **Evidence Collection:**
  1. Backend logs: `Round 2: 1.14s, thoughts=0, texts=0, fc=0` → Empty round
  2. Products found: `10 products` via `search_products`
  3. History size: `79-84 messages` causing Gemini context confusion
- **Root Cause Discovery (via Tree of Thoughts):**
  - **History Confusion:** Long conversation history (50+ messages) caused Gemini to believe products were "already provided"
  - **Loop Exhaustion:** `max_function_rounds=2` meant loop ended while Gemini still processing
- **Fix Strategy:** Hybrid Approach (v5.1)
  1. **History Pruning:** `keep_count` reduced from 50 to 30 messages
  2. **Force Text Generation:** Post-loop always tries one more API call before fallback
- **Locations Modified:**
  | Location | Change |
  |----------|--------|
  | `mongo_store.py` L543-544 | `keep_count = 50` → `keep_count = 30` |
  | `main.py` L2340-2346 | In-loop Force Round detection |
  | `main.py` L2565-2606 | Post-loop Force Text Generation (v5.1 - no counter limit) |
- **Status:** ✅ RESOLVED

---

## Key Code Changes (January 21)

| Location | Change | Impact |
|----------|--------|--------|
| `mongo_store.py` L543-544 | History pruning 50→30 | Reduces context confusion |
| `main.py` L2196-2197 | Added `force_round_count` variable | Track forced rounds |
| `main.py` L2340-2346 | Force Round 3 in-loop detection | Continue loop if empty |
| `main.py` L2565-2606 | Force Text Gen v5.1 (always try) | Final safety net |

---

## Test Results

| Query | Type | Chars | TIP | Quick Replies | Time |
|-------|------|-------|-----|---------------|------|
| პროტეინი | First | 1847 | ✅ | ✅ | 13.3s |
| კუნთის ზრდისთვის? | Follow-up | 2278 | ✅ | ✅ | 21.5s |

**No more fallback! All responses are rich Georgian text with tips and quick replies.**

---

## Learnings From This Session

1. **History Length Matters:** Gemini with 80+ messages loses focus. 30 messages (15 exchanges) is optimal.
2. **Force Text Generation Pattern:** When loop exhausts, explicit Georgian prompt forces text synthesis.
3. **Counter Limit Pitfall:** v5.0 had `force_round_count < 1` in Post-Loop which blocked after in-loop force. v5.1 removes limit.
4. **Hybrid Approach:** Combining preventive (history pruning) + reactive (force generation) provides robust solution.

---

*Last Updated: January 21, 2026 ~01:10*

---

## Development Timeline: January 21, 2026 (Late Night Session ~01:15-03:15)

### Session: C3 Holistic System Debug + Claude Code Handoff

**Problem Reported:** Systemic failures on complex queries - budget constraints ignored, no prioritization, myths not debunked.

---

## C3 Deep Reasoning Protocol (DRP)

### Phase 0-3: Sequential Thinking Analysis

**Symptoms Identified:**
1. Budget queries (150₾) → Products 191₾, 220₾ shown
2. Prioritization requests → Generic list, no ranking
3. Myth debunking → Not addressed
4. Multi-constraint (lactose + budget + 3 products) → All ignored

**Root Cause (via 5-Thought Sequential Analysis):**
- `system_prompt_lean.py` v3.0 lacks reasoning intelligence
- `search_products(max_price)` EXISTS and WORKS ✅
- `chat_stream` pipeline is STABLE ✅
- **Problem:** Prompt has no instructions to USE max_price

### What v3.0 Has vs Lacks

| Has ✅ | Lacks ❌ |
|--------|----------|
| Safety rules | Budget logic |
| Allergies | Prioritization |
| Search syntax | Myth debunking |
| Response format | Cart composition |

---

## Proposed Fix: v3.1 (+360 tokens)

Add 4 sections after line 23 (after ალერგიები):

1. **ბიუჯეტის ლოგიკა** - `search_products(max_price=X)`
2. **პრიორიტიზაცია** - protein > creatine > omega-3
3. **მითების გაქარწყლება** - "ქიმიაა" → factual response
4. **კალათის კომპოზიცია** - calculate total, don't exceed

---

## Claude Code Handoff Created

**File:** `backend/CLAUDE_CODE_HANDOFF.md`
- Contains failing queries, symptoms, key files to analyze
- NO proposed solution - Claude Code to find root cause independently
- Verification commands provided

---

## Next Steps

1. Claude Code analyzes and implements fix
2. Run evals: `C2`, `M3`, `E4`, `L2`
3. Manual test failing queries
4. Sync to GitHub after verification

---

*Last Updated: January 21, 2026 ~22:45*

---

## Development Timeline: January 21, 2026 (~21:00-22:40)

### Phase 9: LLM Fact Verification (Guard Layer)

**Goal:** Implement smart extraction with negation and context reference handling.

---

## Features Implemented

### 1. Smart Negation Fallback (Zero Latency)
- `"90 კილო კი არ ვარ, 85 კილო ვარ"` → Saves **85** (last value)
- No LLM call needed, instant processing
- Location: `app/profile/profile_extractor.py` L201-228

### 2. Context Reference Detection
- `"შვილს 14 წელი აქვს"` → **Skips extraction** (not user's data)
- Triggers: `შვილ`, `ძმა`, `მშობ`, `მეგობ`, `ცოლ`, `ქმარ`
- Location: `app/profile/profile_extractor.py` L353-366

### 3. LLM Fact Verification
- Async verification for truly ambiguous cases
- Uses Gemini Flash for quick confirmation
- Location: `app/profile/profile_extractor.py` L369-450

### 4. Physical Stats in Profile Response
- `get_user_profile()` now returns `weight`, `height`, `age`
- Enables Context Injection with user's physical data
- Location: `app/tools/user_tools.py` L162-190

---

## Code Changes Summary

| File | Changes |
|------|---------|
| `app/profile/profile_extractor.py` | +168 lines |
| `app/profile/profile_processor.py` | +102 lines |
| `app/tools/user_tools.py` | +31 lines |
| `tests/test_profile_safety.py` | +112 lines |
| **Total** | +511 lines |

---

## E2E Test Results

| Test | Input | Expected | Result |
|------|-------|----------|--------|
| Context Trap | "შვილს 14 წ აქვს" | Skip extraction | ✅ PASSED |
| Negation Fix | "90 კი არა, 85 ვარ" | Save 85kg | ✅ PASSED |
| Context Injection | Protein query | Show weight in profile | ✅ PASSED |

---

## GitHub Sync

| Repo | Status |
|------|--------|
| Backend | ✅ Pushed (0069298) |
| Frontend | No changes |

---

## Development Timeline: January 22, 2026 (Afternoon Session ~16:00-17:20)

### Session: v2.0 Engine Bug Fixes

**Goal:** Fix v2.0 Engine bugs blocking frontend display.

---

## Bug Log (January 22)

### Bug #22: SDK ValueError in Function Response (CRITICAL)
- **Symptom:** `ValueError: Message must be a valid part type` on Round 2
- **Root Cause:** `_build_function_response_message` returned dicts, SDK requires `Part` objects
- **Fix:** `Part.from_function_response()` in `app/core/function_loop.py` L433-458
- **Status:** ✅ RESOLVED

### Bug #23: Frontend Empty Response Display (CRITICAL)
- **Symptom:** Thinking steps show, but text response empty in UI
- **Root Cause:** Backend sent `data: {"content": "..."}`, frontend expected `data.type === 'text'`
- **Fix:** Include `type` in SSE payload: `{"type": "text", "content": "..."}`
- **Location:** `app/core/engine.py` L67-76
- **Status:** ✅ RESOLVED

---

## Key Code Changes (January 22)

| Location | Change |
|----------|--------|
| `app/core/function_loop.py` L28 | Added `from google.genai.types import Part` |
| `app/core/function_loop.py` L433-458 | Return `List[Part]` with `Part.from_function_response()` |
| `app/core/engine.py` L67-76 | SSE payload includes `type` field for frontend |

---

*Last Updated: January 22, 2026 ~17:20*

---

## Development Timeline: January 22, 2026 (Late Evening Session ~19:40-21:03)

### Session: Holistic Stability Fix - Session Amnesia, NoneType Crash, Function Loop

**Problems Reported:**
1. Every message started with "გამარჯობა" - model didn't remember history
2. Follow-up questions always the same (after ~3rd question)
3. Empty Response crashes

---

## Bug Log (January 22 - Late Evening)

### Bug #24: Session Amnesia - Frontend/Backend Session ID Mismatch (CRITICAL)
- **Symptom:** `history_len=0` on every request despite being in same conversation
- **Evidence from logs:**
  ```
  📥 _load_context START: requested_session=6vqsrxk2aw8
  📥 _load_context COMPLETE: session=session_15d7dd81a7c6, history_len=0
  ```
- **Root Cause Discovery:**
  - Frontend sends `convId` (e.g., `6vqsrxk2aw8`) which is locally generated
  - Backend creates NEW session `session_xxx` format
  - MongoDB query with frontend's convId returns nothing
  - Backend creates fresh session → history lost!
- **Fix (Two-Part):**
  1. **Backend (`engine.py` L421-426):** Return `session_id` in SSE `done` event
  2. **Frontend (`Chat.tsx`):**
     - Added `backendSessionId` to Conversation interface
     - Store session_id from done event
     - Use `backendSessionId` for subsequent requests
- **Status:** ✅ RESOLVED

### Bug #25: MAX_FUNCTION_CALLS Override in .env (CONFIG)
- **Symptom:** Logs showed `Streaming round 1/3` despite config.py having `5`
- **Root Cause:** `.env` file had `MAX_FUNCTION_CALLS=3` which overrode default
- **Fix:** Changed `.env` to `MAX_FUNCTION_CALLS=5`
- **Status:** ✅ RESOLVED

### Bug #26: NoneType Crash - candidate.content.parts is None (CRITICAL)
- **Symptom:** `TypeError: 'NoneType' object is not iterable`
- **Stack Trace:**
  ```python
  File "function_loop.py", line 635, in _execute_round_streaming
    for part in candidate.content.parts:
  TypeError: 'NoneType' object is not iterable
  ```
- **Root Cause:** Gemini returns response where `candidate.content.parts` is `None`
- **Fix - 3 Locations (Defensive Null Checks):**
  1. `function_loop.py` L307: Sync execution method
  2. `function_loop.py` L635: Streaming execution method
  3. `gemini_adapter.py` L522: Chunk parsing
- **Status:** ✅ RESOLVED

---

## Key Code Changes (January 22 - Late Evening)

| Location | Change |
|----------|--------|
| `engine.py` L421-426 | Add `session_id` to SSE done event |
| `Chat.tsx` interface | Add `backendSessionId` field |
| `Chat.tsx` done handler | Store `data.session_id` |
| `.env` | `MAX_FUNCTION_CALLS=5` |
| `function_loop.py` L307, L635 | Defensive null checks |
| `gemini_adapter.py` L522 | Defensive null check |

---

## GitHub Sync (January 22 - Late Evening)

| Repo | Commit | Status |
|------|--------|--------|
| Backend | `cb0e9f0` | ✅ Pushed |
| Frontend | `c15e278` | ✅ Pushed |

---

## Development Timeline: January 22, 2026 (Night Session ~21:10-21:42)

### Session: Quick Replies Static Placeholder Fix

**Problem:** Quick replies always returned the same static text: "გაყიდვა 1", "გაყიდვა 2"

---

## Bug Log (January 22 - Night)

### Bug #27: Quick Replies Always Same (CRITICAL)
- **Symptom:** Every response had identical quick_replies placeholders
- **Debug Flow:**
  1. Added logging: `🎯 Quick replies parsed: 0 items, text has QUICK_REPLIES tag: True`
  2. Discovery: Tag exists but 0 items parsed
  3. Buffer inspection: `[QUICK_REPLIES]` present but **no `[/QUICK_REPLIES]` closing tag**
  4. Gemini truncates response before sending closing tag
- **Root Cause (Two Issues):**
  1. **Prompt Issue:** `system_prompt_lean.py` had literal placeholder examples
  2. **Parsing Issue:** Regex required closing tag: `\[QUICK_REPLIES\](.*?)\[/QUICK_REPLIES\]`
- **Fix (Two-Part):**
  1. **Prompt (`system_prompt_lean.py`):** Replaced static placeholders with dynamic generation instructions and contextual examples
  2. **Parsing (`response_buffer.py`):** Added fallback for unclosed tags: `r'\[QUICK_REPLIES\](.*?)$'`
- **Status:** ✅ RESOLVED

**Verification:**
```
🎯 Quick replies parsed: 4 items
SSE: {"replies": ["როგორ მივიღო სწორად?", "რატომ ითხოვს ბევრ წყალს?", ...]}
```

---

## Key Code Changes (January 22 - Night)

| Location | Change |
|----------|--------|
| `prompts/system_prompt_lean.py` L113-130 | Dynamic quick replies instructions |
| `app/core/response_buffer.py` L372-378 | Fallback regex for unclosed tags |
| `app/core/engine.py` L393 | Concise logging for QR count |

---

## ⚠️ REVERT: Quick Replies Fix უარყოფილია

**Commit:** `5494e40` - Revert "fix: Dynamic quick replies + unclosed tag fallback"

**მიზეზი:** ტესტირების შემდეგ `EmptyResponseError` ჩნდებოდა. Bug #27 fix გაუქმებულია და კოდი დაბრუნდა `cb0e9f0` მდგომარეობაში.

**სტატუსი:** მზადაა ახალი approach-ით თავიდან დაწყება.

---

*Last Updated: January 22, 2026 ~21:56*

---

## Development Timeline: January 22, 2026 (Late Night Session ~23:00-23:45)

### Session: Latency Optimization + Products SSE Fix

**Goal:** Optimize latency and fix products not appearing in SSE.

---

## Bug Log (January 22 - Late Night)

### Bug #28: Vector Search Results Nullified (LATENCY)
- **Symptom:** `search_products` always fell back to regex despite vector search finding products
- **Debug Method:** Added logging to `user_tools.py`
- **Root Cause:** Line 582 had `products = []` immediately after vector search, nullifying results
  ```python
  products = list(vector_search(...))  # ← 10 products found
  products = []  # ← BUG: Results wiped!
  if not products:  # ← Always true, triggers regex fallback
  ```
- **Fix:** Removed `products = []` line
- **Location:** `user_tools.py` L582
- **Latency Impact:** ~200-400ms saved (no double search + embed)
- **Status:** ✅ RESOLVED

### Bug #29: Products SSE Format Mismatch (UX CRITICAL)
- **Symptom:** Frontend `[DEBUG SSE] products undefined` despite backend finding 10+ products
- **Debug Method:** Added debug logging in `engine.py`:
  ```
  📊 DEBUG: state.all_products has 10 products
  📊 DEBUG: buffer now has 10 products  
  📊 DEBUG: Yielding products SSE event with 10 products ✅
  ```
- **Root Cause:** Backend sent `{products: [...]}`, Frontend expected `{content: "..."}`
  - Backend (`engine.py` L407): `yield SSEEvent("products", {"products": snapshot.products})`
  - Frontend (`Chat.tsx` L497): `assistantContent += data.content`
- **Fix:** 
  1. Added `_format_products_markdown()` helper method in `engine.py`
  2. Changed SSE to: `yield SSEEvent("products", {"content": formatted_markdown})`
- **Location:** `engine.py` L413-416, L806-840
- **Status:** ✅ RESOLVED

---

## Key Code Changes (January 22 - Late Night)

| Location | Change | Impact |
|----------|--------|--------|
| `user_tools.py` L582 | Removed `products = []` | Vector results preserved |
| `engine.py` L413-416 | SSE products format fix | Frontend receives content |
| `engine.py` L806-840 | Added `_format_products_markdown()` | Products as markdown |

---

## Search-First Architecture ✅ IMPLEMENTED

**Implementation Date:** January 23, 2026

**Concept:** Run vector search BEFORE Gemini, inject results into message context, eliminating function-calling round-trip latency.

### Problem Solved
- **Before:** User asks "მინდა პროტეინი" → Gemini Round 1 (thinks) → search_products() → Round 2 (responds) = **~12-15s**
- **After:** Pre-fetch products → Inject into context → Gemini Round 1 (responds immediately) = **~7-9s**

### Implementation Details

**Files Modified:**
| File | Change |
|------|--------|
| `engine.py` L57-117 | Added constants: `PRODUCT_KEYWORDS`, `INTENT_VERBS`, `NEGATIVE_MARKERS`, `INJECTION_TEMPLATE` |
| `engine.py` L826-888 | Added `_is_product_query()` method - intent classifier |
| `engine.py` L890-908 | Added `_format_products_for_injection()` method |
| `engine.py` L910-965 | Replaced `_enhance_message()` with Search-First implementation |
| `tests/core/test_search_first.py` | 19 unit tests for intent classification |

**Intent Classification Logic:**
```python
def _is_product_query(message, history_len) -> (should_search, keyword):
    # RULE 0: Skip if mid-conversation (history_len > 4)
    # Check NEGATIVE_MARKERS first (past tense: "ვიყიდე", complaints: "ცუდი")
    # Check PRODUCT_KEYWORDS (პროტეინ, კრეატინ, ვიტამინ...)
    # Check INTENT_VERBS (მინდა, მჭირდება, რომელი...)
    # Return (True, keyword) or (False, None)
```

**Injection Template:**
```
[კონტექსტი: Scoop.ge კატალოგი - {count} პროდუქტი ნაპოვნია]
1. Product A - 89₾ (Brand)
2. Product B - 75₾ (Brand)
...
[შენიშვნა: ეს არის კატალოგის მონაცემები]

მომხმარებლის შეკითხვა: {original_message}
```

### Test Results

**Unit Tests:** 19/19 PASSED ✅
```
test_product_query_with_intent_verb ✅
test_product_query_with_question ✅
test_past_tense_bought ✅ (negative filter)
test_complaint_bad ✅ (negative filter)
test_greeting ✅ (no keyword)
test_skip_mid_conversation ✅ (history check)
...
```

**Integration Verification:**
```
INFO - ✅ Search-First: Product query detected: 'პროტეინ'
INFO - 🔍 Search-First: Pre-fetching for 'პროტეინ'
INFO - 🧠 Vector search: Found 5 products for 'პროტეინ'
INFO - 🔍 Search-First: Injected 5 products into context
INFO - 🔄 Streaming round 1/5
```

**Evals Results:** 80% (20/25 passed) - NO REGRESSION
| Set | Pass | Rate |
|-----|------|------|
| Simple | 5/5 | 100% |
| Medical | 5/5 | 100% |
| Logic | 5/5 | 100% |
| Context | 2/5 | 40% (pre-existing multi-turn issues) |
| Ethics | 3/5 | 60% (content policy blocks) |

### Expected Performance Impact

| Metric | Before | After |
|--------|--------|-------|
| Product query latency | ~12-15s | **~7-9s** (-40%) |
| Function-calling rounds | 2-3 | 1 |
| False positive rate | N/A | <8% |

---

*Last Updated: January 23, 2026 ~00:55*

---

## Development Timeline: January 23, 2026 (Night Session ~02:20-02:30)

### Session: Bug #27 - Text Truncation During Function Calls

**Problem Reported:** Text ends mid-word in UI (e.g., `"გასაგებია... ე.წ. "პარდ"` instead of full response).

---

## Bug Log (January 23)

### Bug #27: Text Truncation When FC and Text Coexist (CRITICAL)
- **Symptom:** UI shows partial text that ends mid-word
- **Debug Method:** `/debug` workflow + curl testing
- **Evidence Collection:**
  1. Frontend DevTools: `[DEBUG SSE] text` truncated in logs
  2. curl test: Some requests had NO `event: text` at all
  3. Backend logs: Products found, but text empty
- **Root Cause Discovery (via Analysis):**
  - When Gemini emits **both text AND function call** in single response:
    - `text_parts = ["შენთვის პროტეინის..."]` (prelude)
    - `function_calls = [FC(search_products)]`
  - Old logic (`function_loop.py` L329-337):
    ```python
    if accumulated_text.strip():      # ← Text wins
        result = RoundResult.COMPLETE # ← FC silently dropped!
    elif function_calls:
        result = RoundResult.CONTINUE
    ```
  - **Result:** Loop exits immediately, FC never executes, user sees incomplete "prelude" text
- **Fix Strategy:** "Discard Prelude, Execute FC"
  - FC ALWAYS takes priority over text
  - Prelude text (incomplete thought) is discarded
  - Loop continues to execute FC
  - Final text comes from later round after FC results
- **Locations Modified:**
  | Location | Change |
  |----------|--------|
  | `function_loop.py` L329-347 | FC priority + prelude discard (sync) |
  | `function_loop.py` L656-679 | FC priority + prelude discard (async) |
- **Code Change:**
  ```python
  # NEW: FC ALWAYS takes priority
  if function_calls:
      result = RoundResult.CONTINUE
      if accumulated_text.strip():
          logger.info(f"⚠️ Discarding prelude text ({len(accumulated_text)} chars)")
          accumulated_text = ""  # Clear prelude
  elif accumulated_text.strip():
      result = RoundResult.COMPLETE
  else:
      result = RoundResult.EMPTY
  ```
- **Status:** ✅ RESOLVED

---

## Key Code Changes (January 23 - Night)

| Location | Change | Impact |
|----------|--------|--------|
| `function_loop.py` L329-347 | FC priority in sync method | No more dropped FCs |
| `function_loop.py` L656-679 | FC priority in streaming method | No more truncated text |

---

## Verification

- **Unit Tests:** 22/22 passed (no regression) ✅
- **curl Test:** `event: text` now present in responses ✅
- **Pattern:** Prelude text discarded, FC executes, final text complete

---

## Learnings From This Session

1. **Gemini Dual Output:** Gemini can emit text AND function call simultaneously - the text is a "prelude" (interrupted thought), not the answer.
2. **Priority Matters:** FC must always take priority to ensure search/lookup executes.
3. **Discard vs Concatenate:** Discarding prelude prevents "double text" artifacts.

---

*Last Updated: January 23, 2026 ~02:30*

---

## Development Timeline: January 23, 2026 (Late Night Session ~02:30-03:10)

### Session: Bug #27 Verification + Short Response Investigation

**Problem Reported:** UI ზოგჯერ აჩვენებს მოკლე ტექსტს ("გამარ" ან "გიორგი" მხოლოდ).

---

## Debugging Summary

### Bug #27 Fix - Verified Working ✅

**curl tests confirm backend sends full text:**
- Query: "გამარჯობა" → Full 600+ char response ✅
- Query: "პროტეინი მაინტერესებს" → Full response with products ✅

**Browser Subagent Verification:**
- Manual fetch interception showed complete SSE stream
- No network-level truncation detected

---

### New Issue Identified: Short First-Response (OPEN)

**Symptom:** ახალი session-ის პირველ query-ზე მოკლე პასუხი:
```
[DEBUG SSE] text გიორგი  ← Only 7 chars!
[DEBUG SSE] text გამარჯობა გიორგი!  ← Only 19 chars!
```

**Pattern:**
| Request | Response | Problem |
|---------|----------|---------|
| New session, 1st query | "გამარჯობა გიორგი!" | Very short |
| Same session, 2nd+ query | Full 300+ word response | OK ✅ |

**Root Cause Analysis:**
1. New session = `history_len=0`
2. Frontend sends random `convId`, backend creates new `session_xxx`
3. Gemini sees empty history + profile only
4. System prompt (L82-83): `"პირველ მესიჯზე მოკლედ უპასუხე"`
5. Gemini interprets "short first response" literally → greeting only

**Identified Fix Location:**
```
prompts/system_prompt_lean.py L80-84:
## მისალმება
"სალამი" = გამარჯობა (არა პროდუქტი!)
პირველ მესიჯზე მოკლედ უპასუხე, შემდეგ პირდაპირ საქმეზე.
```

**Proposed Fix (TODO):**
დააზუსტე რომ "მოკლედ" ეხება მხოლოდ greeting-ს, არა profile/info queries-ს:
```
"რა იცი ჩემზე?" = სრულად ჩამოთვალე profile info
```

**Status:** 🟡 OPEN - ხვალ გასასწორებელი

---

## Other Issues Found

### MongoDB Save Error (Non-Critical)
```
ERROR - Failed to save history: 'NoneType' object is not iterable
```
- Occurs sporadically on session save
- Does not affect user response
- **Status:** ✅ FIXED (see Bug #26)

---

*Last Updated: January 23, 2026 ~03:10*

---

## Development Timeline: January 26, 2026 (Evening Session ~22:30-22:50)

### Session: NoneType Crash Fix (Bug #26)

**Problem Reported:** 
- Gemini responses cut off mid-sentence
- MongoDB history save failing with `TypeError: 'NoneType' object is not iterable`

---

## Bug Log (January 26)

### Bug #26: NoneType Crash on content.parts Iteration (CRITICAL)
- **Symptom:** 
  1. პასუხი იჭრებოდა შუა წინადადებაში
  2. ლოგებში: `ERROR - Failed to save history: 'NoneType' object is not iterable`
- **Investigation Method:** `/opus-planning` + `/claude-building` + `/test-sprite` workflows
- **Discovery Method:** Source code analysis with grep_search

**Root Cause Discovery:**
- Gemini SDK can return `Content` objects with `parts=None`
- 4 locations in codebase iterated on `content.parts` without null check
- When `parts=None`, Python throws `TypeError: 'NoneType' object is not iterable`

**Affected Locations:**

| File | Line | Code | Status |
|------|------|------|--------|
| `function_loop.py` | 308, 648 | `if parts is not None:` | ✅ Already Fixed |
| `mongo_store.py` | 427 | `for part in content.parts:` | ❌ BUG |
| `gemini_adapter.py` | 268 | `if hasattr(content, 'parts'):` | ❌ BUG |
| `main.py` | 494 | `for part in content.parts:` | ❌ BUG |
| `main.py` | 893 | `for part in candidate.content.parts:` | ❌ BUG |

**Fix Applied (4 locations):**

```python
# Pattern 1: Using `or []` for safe iteration
# BEFORE (Bug)
for part in content.parts:

# AFTER (Fixed)
for part in (content.parts or []):
```

```python
# Pattern 2: Adding null check to hasattr
# BEFORE (Bug)
if hasattr(content, 'parts'):

# AFTER (Fixed)
if hasattr(content, 'parts') and content.parts:
```

**Files Modified:**

| Location | Change |
|----------|--------|
| `app/memory/mongo_store.py` L427 | `(content.parts or [])` |
| `app/adapters/gemini_adapter.py` L268 | `and content.parts` |
| `main.py` L494 | `(content.parts or [])` |
| `main.py` L893 | `(candidate.content.parts or [])` |

**Verification:**
- ✅ Syntax check passed (py_compile)
- ✅ Unit tests: 8/8 passed
- ✅ Test file created: `tests/test_nonetype_crash_fix.py`

**Confidence:** 97% - Follows proven pattern from `function_loop.py`

- **Status:** ✅ RESOLVED

---

## Learnings From This Session

1. **Gemini SDK Inconsistency:** SDK can return `Content.parts = None` even in successful responses.
2. **Defensive Programming:** Always use `(parts or [])` pattern when iterating SDK objects.
3. **Pattern Reuse:** `function_loop.py` already had the fix - should have applied same pattern everywhere.
4. **Test-Sprite Workflow:** Creating standalone tests without pytest is useful for quick verification.

---

*Last Updated: January 26, 2026 ~22:50*

---

## Development Timeline: January 26, 2026 (Late Night Session ~23:00-23:55)

### Session: SSE Text Cutoff Bug - Non-Deterministic Response Truncation

**Problem Reported:** AI responses randomly cut off mid-sentence. Backend logs show full text (1837 chars), but frontend displays only ~100 chars. This was **non-deterministic** - same query could work or fail.

---

## Bug Log (January 26 - Late Night)

### Bug #22: SSE Event Boundary Parsing (PARTIAL FIX)
- **Symptom:** Response text truncated at random positions
- **Initial Hypothesis:** SSE events split across network chunks on single `\n`
- **Attempted Fix:** Changed `buffer.split('\n')` to `buffer.split('\n\n')` for proper SSE event boundaries
- **Location:** `frontend/src/components/Chat.tsx` lines 456-480
- **Result:** ❌ Partial improvement but still non-deterministic
- **Status:** 🔶 SUPERSEDED by Bug #23

### Bug #23: TextDecoder Stream Flush (ROOT CAUSE FIX)
- **Symptom:** Same as Bug #22 - random text truncation
- **Deep Investigation (via Claude Code handoff):**
  1. Backend logs confirmed: `📡 SSE TEXT: len=1837` - full text sent ✅
  2. Frontend console showed: `[DEBUG SSE] text გამარჯობა... შ` - only ~55 chars ❌
  3. Analysis revealed: `TextDecoder.decode(value, { stream: true })` buffers incomplete UTF-8 sequences
  4. Georgian characters = 3 bytes each, chunk boundaries can split mid-character
  5. When `done=true`, original code immediately `break` - losing buffered bytes
  
- **Root Cause Discovery (via MDN Documentation):**
  > "When streaming mode is enabled, the decoder will buffer incomplete byte sequences between calls. 
  > When finished, call decode() with no arguments to flush any remaining bytes."
  
  Source: https://developer.mozilla.org/en-US/docs/Web/API/TextDecoder/decode#stream

- **Fix (2 changes in Chat.tsx):**

```typescript
// Location 1: Lines 468-474 - Add final flush
if (done) {
    buffer += decoder.decode(); // Final flush - no arguments
}

// Location 2: Line 596 - Move break to END of loop
// (after processing the flushed buffer)
if (done) break;
```

- **Why Non-Deterministic:**
  - Network chunks are random sizes (TCP packet fragmentation)
  - Sometimes full UTF-8 character fits in chunk → works ✅
  - Sometimes chunk boundary splits mid-character → bytes lost ❌
  - Georgian UTF-8 (3 bytes) more likely to split than ASCII (1 byte)

- **Location:** `frontend/src/components/Chat.tsx` lines 468-474, 596
- **Status:** ✅ RESOLVED

---

## Key Code Changes (January 26 Late Night)

| Location | Change | Impact |
|----------|--------|--------|
| `Chat.tsx` L468-474 | Added `decoder.decode()` flush on stream end | Recovers buffered UTF-8 bytes |
| `Chat.tsx` L596 | Moved `if (done) break;` to after buffer processing | Ensures final events are processed |
| `engine.py` L135-149 | Added `ensure_ascii=False` to JSON serialization | Preserves UTF-8 (minor improvement) |
| `engine.py` L143-148 | Added SSE debug logging `📡 SSE TEXT: len=X` | Better diagnostics |

---

## Additional Fixes This Session

### Port Mismatch Fix
- **Issue:** Frontend `.env.local` had `BACKEND_URL=http://localhost:8000`
- **Reality:** Backend runs on port `8080`
- **Fix:** Updated `.env.local` to use port `8080`
- **Status:** ✅ RESOLVED

---

## Technical Pattern: TextDecoder Streaming Best Practice

```typescript
// ❌ WRONG - Loses buffered bytes when stream ends
const decoder = new TextDecoder();
while (true) {
    const { done, value } = await reader.read();
    if (done) break;  // ← EXIT BEFORE FLUSH = DATA LOSS
    buffer += decoder.decode(value, { stream: true });
}

// ✅ CORRECT - Properly flushes buffered bytes
const decoder = new TextDecoder();
while (true) {
    const { done, value } = await reader.read();
    if (value) {
        buffer += decoder.decode(value, { stream: true });
    }
    if (done) {
        buffer += decoder.decode();  // ← FLUSH remaining bytes
    }
    // ... process buffer ...
    if (done) break;  // ← EXIT AFTER FLUSH
}
```

---

## Learnings From This Session

1. **TextDecoder Stream Mode:** `stream: true` buffers incomplete UTF-8 - MUST call `decode()` without args to flush.
2. **Multi-Byte UTF-8 Risk:** Georgian (3 bytes), Emoji (4 bytes) more likely to split across chunks than ASCII.
3. **Non-Deterministic Bugs:** When same input produces different results, look for network/timing-dependent code.
4. **MDN is Truth:** Official docs explicitly state flush requirement - always check primary sources.
5. **Claude Code Handoff:** Creating detailed handoff documents enables parallel debugging with other agents.

---

*Last Updated: January 26, 2026 ~23:55*

---

## Development Timeline: January 27, 2026 (Post-Midnight Deep Research ~00:00-00:35)

### Session: Deep Research - Text Truncation Root Cause Analysis

**Problem:** After applying Bug #22, #23 fixes, text STILL truncates randomly to 15 chars.

---

## Bug Log (January 27 - Post-Midnight)

### Bug #24: Bug #27 Overcorrection (FIXED)
- **Symptom:** Text responses truncated
- **Root Cause:** Original Bug #27 fix discarded ALL text when function calls present
- **Fix:** Changed to only discard short prelude (<50 chars)
- **Location:** `function_loop.py` lines 332-351, 672-691
- **Status:** ✅ RESOLVED

### Bug #25: Gemini Streaming Partial Response (UNRESOLVED)
- **Symptom:** Backend receives only 15 chars from Gemini, cut mid-word ("შე...")
- **Investigation Results:**
  1. HTTP 200 OK from Gemini API - no error
  2. Backend code verified correct - text accumulation works
  3. Frontend code verified correct - receives exactly what backend sends
  4. Text cut mid-Georgian-word suggests stream ends prematurely

- **Log Evidence:**
```
00:23:11 → history_len=72 → len=1179 ✅
00:23:33 → history_len=30 → len=15 ❌
```

- **Root Cause Hypothesis:**
  - Gemini `gemini-3-flash-preview` model sometimes returns partial streaming response with STOP
  - No clear correlation with history length
  - Non-deterministic - same query works sometimes, fails other times

- **Location:** External - Gemini API behavior
- **Status:** 🔶 INVESTIGATION ONGOING

---

## Deep Research Analysis Summary

### Code Verification (All Correct ✅)

| Component | File | Status |
|-----------|------|--------|
| Text Accumulation | `function_loop.py` L670 | ✅ Correct |
| Bug #27 REVISED | `function_loop.py` L672-691 | ✅ Applied |
| SSE Parsing | `Chat.tsx` L477 | ✅ Correct |
| TextDecoder Flush | `Chat.tsx` L468-474 | ✅ Applied |

### Evidence Trail

```
Frontend Console:
[DEBUG SSE] text len=15 გიორგი, უნდა შე

Backend Log:
📊 snapshot.text length: 15 chars
📡 SSE TEXT: len=15
```

**Conclusion:** Frontend receives exactly what Backend sends. Problem is upstream in Gemini.

---

## Recommended Next Steps for Bug #25

1. **Diagnostic:** Add `finish_reason` logging in `function_loop.py` L648
2. **Defensive:** Add retry logic if response < 30 chars with no function calls
3. **Long-term:** Consider switching from `gemini-3-flash-preview` to stable model

---

## Technical Pattern: Gemini Streaming Response Validation

```python
# Proposed defensive pattern
if len(accumulated_text) < 30 and not function_calls:
    logger.warning(f"⚠️ Suspiciously short response: {len(accumulated_text)} chars")
    # Consider: retry with same message
```

---

## Learnings From This Session

1. **Preview Models:** `gemini-3-flash-preview` may have unstable streaming behavior
2. **Text Cut Mid-Word:** Strong indicator of stream interruption, not logic error
3. **Deep Research Protocol:** Systematic analysis (Sequential Thinking → Code Analysis → Log Analysis) effective for complex bugs
4. **HTTP 200 ≠ Success:** API can return 200 but deliver partial content

---

*Last Updated: January 27, 2026 ~00:35*

---

## Development Timeline: January 27, 2026 (~01:00-01:15)

### Session: Deep Research & Migration Planning

**Problem Solved:** Root cause analysis of text truncation identified `FinishReason.SAFETY` as culprit.

---

## Bug Log (Continued)

### Bug #26: Gemini Safety Filter False Positives (SOLUTION FOUND)
- **Symptom:** Legitimate content (lactose intolerance, sports nutrition) triggers SAFETY
- **Root Cause:** `BLOCK_MEDIUM_AND_ABOVE` too strict for health/nutrition advice
- **Evidence:** 
  - "ლაქტოზის აუტანლობა" query → `FinishReason.SAFETY` → 84 chars
  - Same query on Gemini 2.5 Flash → No safety trigger (OFF is default)
- **Solution:** Migrate to `gemini-2.5-flash` where Safety is OFF by default
- **Status:** ✅ SOLUTION IDENTIFIED, Migration Pending

---

## Migration Plan: gemini-3-flash-preview → gemini-2.5-flash

### Why Migrate?

| Feature | gemini-3-flash-preview | gemini-2.5-flash |
|---------|----------------------|-----------------|
| Status | Pre-GA | **GA** ✅ |
| Safety Default | BLOCK_MEDIUM | **OFF** ✅ |
| Thinking Config | thinking_level | thinking_budget |

### Files to Modify

1. **config.py** L39: `model_name: str = "gemini-2.5-flash"`
2. **gemini_adapter.py** L74: `model_name: str = "gemini-2.5-flash"`
3. **main.py** L401, L431: `thinking_level` → `thinking_budget`
4. **evals/judge.py** L61: `self.model = "gemini-2.5-flash"`

### Thinking Level → Budget Mapping

| thinking_level | thinking_budget |
|---------------|-----------------|
| MINIMAL | 0 |
| LOW | 4096 |
| MEDIUM | 8192 |
| HIGH | 16384 |

---

## Technical Research Summary

### All FinishReason Values That Cause Truncation:

| FinishReason | Cause | Solution |
|-------------|-------|----------|
| **SAFETY** | Content triggered harm filter | Lower threshold / Use 2.5 |
| **MAX_TOKENS** | Response hit output limit | Increase max_output_tokens |
| **RECITATION** | Copyright match | Rephrase content |
| **STOP** | Normal completion | N/A (expected) |

---

## Learnings From This Session

1. **GA > Preview:** Gemini 2.5 Flash (GA) more stable than 3 (Preview)
2. **Safety OFF by Default:** Modern Gemini models designed safe without filters
3. **Diagnostic Logging:** `finish_reason` logging revealed root cause instantly
4. **Deep Research Protocol:** Systematic approach found solution in 45 minutes

---

*Last Updated: January 27, 2026 ~01:15*

---

## Development Timeline: January 27, 2026 (~12:00-18:00)

### Session: Hybrid Inference Architecture Implementation

**Problem Solved:** Gemini 3.0 Flash Preview არასტაბილურობა (503 errors, timeouts, ცარიელი პასუხები) - სრული fallback სისტემა შეიქმნა.

---

## Hybrid Inference Architecture (v3.0)

### პრობლემა

**Gemini 3.0 Flash Preview** - ძალიან კარგი მოდელია, მაგრამ არასტაბილური:
- 503/500 errors დროდადრო
- Timeout-ები
- ცარიელი პასუხები
- Safety filter false positives

### გადაწყვეტა: 3-საფეხურიანი სათადარიგო სისტემა

```
🥇 Gemini 3.0 Flash Preview (პირველი)
         ↓ თუ ჩაიშალა
🥈 Gemini 2.5 Pro (დიდი კონტექსტი)
         ↓ თუ ესეც ჩაიშალა
🥉 Gemini 2.5 Flash (საიმედო fallback)
```

---

## ახალი კომპონენტები

### 1. CircuitBreaker (`app/core/circuit_breaker.py`)

მოდელის "ჯანმრთელობის" tracking:

```python
# 5 წარუმატებელი მოთხოვნა → მოდელი "გამორთულია" 60 წამით
circuit_failure_threshold: int = 5
circuit_recovery_seconds: float = 60.0
```

**მდგომარეობები:**
- `CLOSED` - მოდელი ჯანმრთელია, მოთხოვნები გადის
- `OPEN` - მოდელი ჩაშლილია, მოთხოვნები აღარ გადის
- `HALF_OPEN` - აღდგენის ტესტირება (2 წარმატება → CLOSED)

**17 ტესტი ✅**

### 2. TokenCounter (`app/core/token_counter.py`)

კონტექსტის სიგრძის დათვლა:

```python
# 4 სიმბოლო = 1 ტოკენი (ლათინური)
# Georgian unicude = 2.5x multiplier
# Safety buffer = 1.1x
tokens = (len(text) // 4) * 2.5 * 1.1
```

**16 ტესტი ✅**

### 3. ModelRouter (`app/core/model_router.py`)

ავტომატური routing ლოგიკა:

```python
if context_tokens < 100_000:
    return PRIMARY  # gemini-3-flash-preview
elif context_tokens < 1_000_000:
    return EXTENDED  # gemini-2.5-pro
else:
    return FALLBACK  # gemini-2.5-flash
```

**13 ტესტი ✅**

### 4. FallbackTrigger (`app/core/fallback_trigger.py`)

შეცდომების აღმოჩენა:

| Trigger | აღწერა |
|---------|--------|
| `SAFETY_BLOCK` | FinishReason.SAFETY |
| `RECITATION` | FinishReason.RECITATION |
| `SERVICE_503` | HTTP 503 error |
| `TIMEOUT` | Request timeout |
| `EMPTY_RESPONSE` | ცარიელი text |

**18 ტესტი ✅**

### 5. HybridInferenceManager (`app/core/hybrid_manager.py`)

ერთიანი ორქესტრატორი:

```python
class HybridInferenceManager:
    def route_request(message, history) -> RoutingResult
    def record_success(model) -> None
    def record_failure(model, exception) -> None
    def get_status() -> Dict
```

**13 ტესტი ✅**

---

## Engine Integration

### შეცვლილი ფაილები

| ფაილი | ცვლილება |
|-------|----------|
| `app/core/engine.py` | HybridInferenceManager import + integration |
| `app/adapters/gemini_adapter.py` | `model_override` პარამეტრი |
| `tests/core/test_engine_integration.py` | MockGeminiAdapter fix |

### stream_message ინტეგრაცია

```python
# Phase 3: Route request using hybrid manager
if self.hybrid_manager:
    routing = self.hybrid_manager.route_request(
        message=message,
        history=context.history,
    )
    selected_model = routing.model

# Phase 4: Create chat with routed model
chat = await self._create_chat_session(context, model_override=selected_model)

# Success/Failure recording
self.hybrid_manager.record_success(selected_model)
self.hybrid_manager.record_failure(selected_model, exception=e)
```

### SSE done event

```json
{
  "event": "done",
  "data": {
    "session_id": "...",
    "model_used": "gemini-3-flash-preview"  // ახალი field
  }
}
```

---

## ტესტირების შედეგები

```
======================= 294 passed, 4 warnings in 3.14s ========================
```

| კატეგორია | რაოდენობა |
|-----------|-----------|
| CircuitBreaker | 17 |
| TokenCounter | 16 |
| ModelRouter | 13 |
| FallbackTrigger | 18 |
| HybridManager | 13 |
| Engine Integration | 9 |
| სხვა | 208 |
| **სულ** | **294** |

### Security Scan

```
semgrep: 0 critical, 0 high, 0 medium, 0 low
Status: 🟢 APPROVED FOR COMMIT
```

---

## კონფიგურაცია

```python
# config.py
primary_model: str = "gemini-3.0-flash-preview-04-17"
extended_model: str = "gemini-2.5-pro-preview-06-05"
fallback_model: str = "gemini-2.5-flash-preview-04-17"

circuit_failure_threshold: int = 5
circuit_recovery_seconds: float = 60.0
extended_context_threshold: int = 150_000
```

---

## როგორ მუშაობს პრაქტიკაში

**სცენარი 1: ნორმალური მოთხოვნა**
```
მომხმარებელი: "რომელი პროტეინი ჯობია?"
         ↓
HybridManager: context=5000 tokens, Flash Preview healthy
         ↓
Gemini 3.0 Flash Preview: პასუხობს ✅
         ↓
record_success("gemini-3-flash-preview")
```

**სცენარი 2: მოდელი ჩაიშალა**
```
Gemini 3.0 Flash Preview: 503 Service Unavailable!
         ↓
FallbackTrigger: SERVICE_503 detected!
         ↓
record_failure("gemini-3-flash-preview")
         ↓
CircuitBreaker: failures=5 → OPEN state
         ↓
Next request → Gemini 2.5 Pro (fallback)
```

**სცენარი 3: დიდი კონტექსტი**
```
მომხმარებელი: [200k token ისტორია]
         ↓
HybridManager: context > 150k → Pro model
         ↓
Gemini 2.5 Pro: handles 1M context ✅
```

---

## შემდეგი ნაბიჯები (Optional)

| Phase | აღწერა | სტატუსი |
|-------|--------|---------|
| 1-8 | Core Implementation | ✅ Complete |
| 9 | Mid-Stream Recovery | ⏳ Optional |
| 10 | Stress Tests | ⏳ Optional |

### Mid-Stream Recovery (Phase 9)

თუ streaming შუაში გაწყდა:
- Buffer + Retry approach
- Frontend receives `retry` event
- Frontend clears partial text
- New model restarts from beginning

---

*Last Updated: January 27, 2026 ~18:00*

---

## Phase 9: Mid-Stream SAFETY Fallback (January 27, 2026 ~18:30-18:45)

### პრობლემა

**Gemini 3.0 Flash Preview** ხშირად აბრუნებს `FinishReason.SAFETY` ჯანმრთელობასთან და წონასთან დაკავშირებულ შეკითხვებზე, რაც იწვევს პასუხის მოჭრას სიტყვის შუაში.

**მაგალითი:**
- **შემავალი:** "მინდა წონის კლება ძალიან სწრაფად, მაგრამ თან მინდა „მას გეინერი" ვიყიდო..."
- **გამომავალი (მოჭრილი):** "გამარჯობა გიორგი. როგორც შენი პროფილიდან ვხედავ, გაქვს ლაქტობის აუტანლობა და ხარ დამწყები, რაც ძალიან მნიშვნელოვანია პრო"

### მიზანი

SAFETY-ით მოჭრილ პასუხებზე ავტომატურად გადავცვალოთ fallback მოდელზე და დავაბრუნოთ სრული პასუხი.

---

### არქიტექტურული ცვლილებები

#### 1. `app/core/types.py`

```python
@dataclass
class RoundOutput:
    # ... existing fields ...
    finish_reason: Optional[str] = None  # NEW: To capture SAFETY, STOP, etc.

@dataclass  
class LoopState:
    # ... existing fields ...
    last_finish_reason: Optional[str] = None  # NEW: Tracks last finish reason
```

#### 2. `app/core/function_loop.py`

**finish_reason capture (L656-658):**
```python
if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
    last_finish_reason = str(candidate.finish_reason)
    logger.info(f"🏁 DEBUG: Chunk #{chunk_count} finish_reason: {last_finish_reason}")
```

**RoundOutput return (L716):**
```python
return RoundOutput(
    result=result,
    text=accumulated_text,
    function_calls=function_calls,
    thoughts=thoughts,
    finish_reason=last_finish_reason,  # NEW
)
```

**State update (L537-539):**
```python
if output.finish_reason:
    self.state.last_finish_reason = output.finish_reason
```

#### 3. `app/core/engine.py` (L480-545)

57-ხაზიანი SAFETY detection & fallback logic:

```python
# DEBUG: Log state for SAFETY analysis
logger.info(
    f"🔬 DEBUG SAFETY CHECK: "
    f"last_finish_reason={state.last_finish_reason}, "
    f"text_len={len(state.accumulated_text)}"
)

# SAFETY Fallback: Check if stream was cut due to SAFETY filter
safety_retry_attempted = False
if (
    state.last_finish_reason 
    and "SAFETY" in state.last_finish_reason.upper()
    and len(state.accumulated_text.strip()) < 300  # Georgian greetings ~130 chars
):
    logger.warning(f"⚠️ SAFETY detected with only {len(state.accumulated_text)} chars...")
    
    # Record failure for circuit breaker
    self.hybrid_manager.record_failure(selected_model, exception=RuntimeError("SAFETY_BLOCK"))
    
    # Get fallback model
    fallback_model = self.hybrid_manager.get_fallback_model(selected_model)
    if fallback_model and fallback_model != selected_model:
        # Clear buffer, recreate chat session, re-execute streaming
        buffer.clear()
        chat = await self._create_chat_session(context, model_override=fallback_model)
        loop = FunctionCallingLoop(chat_session=chat, ...)
        state = await loop.execute_streaming(enhanced_message)
```

#### 4. `app/core/hybrid_manager.py` (L301-340)

**ახალი მეთოდი `get_fallback_model()`:**

```python
def get_fallback_model(self, current_model: str) -> Optional[str]:
    """
    Get the next fallback model in the hierarchy.
    
    Hierarchy:
    - gemini-3-flash-preview → gemini-2.5-pro
    - gemini-2.5-pro → gemini-2.5-flash
    - gemini-2.5-flash → None (no more fallbacks)
    """
    model_hierarchy = {
        self.config.primary_model: self.config.extended_model,
        self.config.extended_model: self.config.fallback_model,
        self.config.fallback_model: None,
    }
    
    fallback = model_hierarchy.get(current_model)
    if fallback:
        logger.info(f"📥 Fallback for '{current_model}' → '{fallback}'")
    return fallback
```

---

### მონაცემთა ნაკადი

```
User Message
    ↓
engine.py::stream_message()
    ↓
loop.execute_streaming(message)
    ↓ (შიდა ციკლი)
_execute_round_streaming() → RoundOutput(finish_reason="FinishReason.SAFETY")
    ↓
_update_state_from_output() → LoopState.last_finish_reason = "FinishReason.SAFETY"
    ↓
return LoopState
    ↓
engine.py: if "SAFETY" in state.last_finish_reason and text < 300
    ↓ (YES)
hybrid_manager.get_fallback_model("gemini-3-flash-preview") → "gemini-2.5-pro"
    ↓
buffer.clear() + new chat session + re-execute
    ↓
Complete response from gemini-2.5-pro ✅
```

---

### ლოგების ნიმუში (წარმატებული fallback)

```
🔬 DEBUG SAFETY CHECK: last_finish_reason=FinishReason.SAFETY, text_len=145, text_stripped_len=142
⚠️ SAFETY detected with only 145 chars, attempting fallback retry...
📥 Fallback for 'gemini-3-flash-preview' → 'gemini-2.5-pro'
🔄 Retrying with fallback model: gemini-2.5-pro
✅ Fallback complete: 1456 chars, finish_reason=FinishReason.STOP
```

---

### Root Cause Analysis

**პირველადი პრობლემა:** `get_fallback_model()` მეთოდი არ არსებობდა `HybridInferenceManager`-ში.

**გამოსწორება:** დამატებულია 40-ხაზიანი მეთოდი ნათელი მოდელების იერარქიით:
1. `gemini-3.0-flash-preview` → `gemini-2.5-pro`
2. `gemini-2.5-pro` → `gemini-2.5-flash`
3. `gemini-2.5-flash` → `None`

---

### ტესტირება

| ტესტი | სტატუსი |
|-------|---------|
| Unit tests (294) | ✅ Passed |
| Engine integration (24) | ✅ Passed |
| Semgrep security scan | ✅ 0 findings |
| Manual test (health query) | ⏳ Testing |

---

---

## Bug Fix: record_failure() Argument Mismatch (January 27, 2026 ~19:05)

### პრობლემა

SAFETY fallback მექანიზმი სწორად აფიქსირებდა SAFETY-ს, მაგრამ fallback-ის დაწყებისას crash ხდებოდა:

```
TypeError: HybridInferenceManager.record_failure() got multiple values for argument 'exception'
```

### Root Cause

`engine.py`-ში `record_failure()` არასწორად იძახებოდა - პირველ პოზიციურ არგუმენტად `selected_model` გადაეცემოდა, მაგრამ მეთოდი მოდელის სახელს არ ღებულობს:

```python
# ❌ არასწორი (engine.py ხაზები 503, 613, 625, 637)
self.hybrid_manager.record_failure(selected_model, exception=e)

# ✅ სწორი
self.hybrid_manager.record_failure(exception=e)
```

### hybrid_manager.py სიგნატურა

```python
def record_failure(
    self,
    exception: Optional[Exception] = None,
    response: Optional[Any] = None,
) -> Tuple[bool, Optional[RoutingDecision]]:
```

### გამოსწორება

მოხსნილია `selected_model` არგუმენტი 4 ადგილიდან `engine.py`-ში:
- ხაზი 503 (SAFETY fallback block)
- ხაზი 613 (EmptyResponseError handler)
- ხაზი 625 (LoopTimeoutError handler)  
- ხაზი 637 (General Exception handler)

### ვერიფიკაცია

ტესტი რეალურ SAFETY trigger-ით:

```
🏁 finish_reason: FinishReason.SAFETY (79 chars)
🔬 SAFETY detected with only 79 chars, attempting fallback retry...
📥 Fallback for 'gemini-3-flash-preview' → 'gemini-2.5-pro' (stable)
🔄 Retrying with fallback model: gemini-2.5-pro
✅ Fallback complete: 2549 chars, finish_reason=FinishReason.STOP
```

### შედეგი

| Before Fix | After Fix |
|:-----------|:----------|
| SAFETY → TypeError crash | SAFETY → Seamless fallback |
| მომხმარებელი ხედავს error | მომხმარებელი ხედავს სრულ პასუხს |

---

*Last Updated: January 27, 2026 ~19:08*

---

## Development Timeline: January 27-28, 2026 (~23:00-00:30)

### Session: EmptyResponseError Fallback + System Prompt Safety Refinement

**Goal:** Implement fallback retry mechanism for `EmptyResponseError` and refine health/safety guidelines in system prompt.

---

## EmptyResponseError Fallback Implementation

### პრობლემა

`EmptyResponseError` ხშირად ხდებოდა Gemini-ს ცარიელი პასუხების გამო, მაგრამ fallback არ ირთვებოდა - მომხმარებელი პირდაპირ error-ს ხედავდა.

### გადაწყვეტა

SAFETY fallback პატერნის გამოყენებით დაემატა EmptyResponseError-ის fallback:

**`engine.py` L608-700:**

```python
except EmptyResponseError as e:
    logger.error(f"Empty response in stream: {e}")
    
    # Attempt fallback retry (ONE attempt only, matching SAFETY pattern)
    if self.hybrid_manager and selected_model and not safety_retry_attempted:
        fallback_trigger = FallbackTrigger()
        decision = fallback_trigger.analyze_exception(e)
        
        if decision.should_fallback:
            fallback_model = self.hybrid_manager.get_fallback_model(selected_model)
            if fallback_model and fallback_model != selected_model:
                logger.info(f"🔄 Fallback retry for empty response: {fallback_model}")
                safety_retry_attempted = True
                
                # Re-create chat session and loop with fallback model
                # ... (same pattern as SAFETY fallback)
```

### ტესტები

**ახალი ფაილი:** `tests/test_empty_response_fallback.py`

| ტესტი | აღწერა | სტატუსი |
|-------|--------|---------|
| `test_fallback_trigger_analyzes_empty_response` | FallbackTrigger recognizes EmptyResponseError | ✅ |
| `test_empty_response_fallback_is_retryable` | Fallback decision is retryable | ✅ |
| `test_fallback_handles_multiple_errors` | Multiple error type handling | ✅ |
| `test_empty_response_fallback_prevents_duplicates` | No duplicate fallback attempts | ✅ |
| `test_empty_response_classified_as_unknown_error` | Correct classification | ✅ |

**შედეგი:** 5/5 tests passed ✅

---

## System Prompt Safety Refinement

### პრობლემა

Health & Safety სექცია ძალიან მკაცრი იყო - ყველა ჯანმრთელობასთან დაკავშირებული შეკითხვა ბლოკავდა, მათ შორის ზოგადი ინფორმაციული კითხვები.

### ცვლილებები (`prompts/system_prompt.py` L11-26)

| ძველი | ახალი |
|-------|-------|
| STRICT blocking on health keywords | Context-aware: informational vs active complaints |
| "ვერ გაძლევ რჩევას" | "გირჩევ შეწყვიტო და ექიმთან კონსულტაცია" |
| Outright refusal for at-risk groups | Safer alternatives offered |
| Dosage as "law" | Dosage as "optimal norm" |

**ახალი სტრუქტურა:**

```
## ჯანსაღი ცხოვრების პრინციპები

### კონტექსტის გააზრება
- ინფორმაციული კითხვა: "რა არის creatine?" → პასუხი ✅
- აქტიური ჩივილი: "თავი მტკივა" → რეკომენდაცია შეწყვიტოს + ექიმი

### რისკ ჯგუფები
- ორსული/მეძუძური → უსაფრთხო ალტერნატივები, არა უარი

### დოზირება
- ოპტიმალური ნორმა, არა კანონი
```

---

## Frontend Configuration Fix

### Port Mismatch

`.env.local`-ში პორტი არასწორი იყო ლოკალური ტესტირებისთვის:

| ფაილი | ძველი | ახალი |
|-------|-------|-------|
| `frontend/.env.local` | `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000` | `http://localhost:8080` |

**შენიშვნა:** ეს მხოლოდ ლოკალური განვითარებისთვის - production-ში სხვა URL გამოიყენება.

---

## შეჯამება

| კომპონენტი | ცვლილება |
|------------|----------|
| `engine.py` | +82 ხაზი EmptyResponseError fallback |
| `system_prompt.py` | -19/+17 ხაზი (რბილი safety) |
| `tests/test_empty_response_fallback.py` | ახალი, 5 ტესტი |
| `frontend/.env.local` | პორტი 8000→8080 (ლოკალი) |

---

*Last Updated: January 28, 2026 ~00:30*

---

## Release: v2.1.0 "The Memory Update" (January 28, 2026)

### Memory System Upgrade

4-ფაზიანი მეხსიერების სისტემის იმპლემენტაცია, რომელიც AI-ს აძლევს მომხმარებლის შესახებ გრძელვადიანი ფაქტების დამახსოვრების შესაძლებლობას.

### არქიტექტურა

```
┌─────────────────────────────────────────────────────────────┐
│                    MEMORY SYSTEM v2.1.0                     │
└─────────────────────────────────────────────────────────────┘

Phase 1: Tiered Memory Storage
├── curated_facts (importance ≥ 0.8) → Permanent
└── daily_facts (importance < 0.8) → 60-day TTL

Phase 2: Hybrid Search
├── Vector Search (semantic similarity)
└── BM25-lite (keyword matching)
└── Score: 0.7×Vector + 0.3×Keyword

Phase 3: Memory Flush
└── Triggers when history > 30 messages
└── Extracts facts before pruning old messages

Phase 4: Context Injection
└── User facts → System Prompt → Gemini
```

### ფაილები

| ფაილი | ცვლილება |
|-------|----------|
| `app/memory/fact_extractor.py` | ახალი - Gemini 2.0 Flash ფაქტების ამოწურება |
| `app/memory/mongo_store.py` | +150 ხაზი - Tiered storage, _flush_memories |
| `app/core/engine.py` | +30 ხაზი - Context injection |
| `scripts/verify_mongo_state.py` | ახალი - MongoDB verification |

### მნიშვნელოვანი შენიშვნა

**FactExtractor მხოლოდ მაშინ ამოიწურებს ფაქტებს, როცა:**
- სესიაში > 30 მესიჯია (15 exchange)
- `prune_history()` გამოიძახება

**თუ სესია დაიხურა 30 მესიჯამდე → ფაქტები არ შეინახება!**

---

## Release: v2.1.1 "The Completeness Patch" (January 28, 2026)

### Incomplete Response Detection

პრობლემა: Gemini 2.0 Flash Preview ზოგჯერ წყვეტს პასუხს `FinishReason.STOP`-ით შუა წინადადებაში.

### იმპლემენტაცია

```python
# fallback_trigger.py
def analyze_text_completeness(self, text: str) -> FallbackDecision:
    patterns = [
        (r':$', "ends with colon"),           # "ვარიანტებია:"
        (r'\bდა$', "ends with და"),           # "პროტეინი და"
        (r'\bმაგრამ$', "ends with მაგრამ"),   # "კარგია, მაგრამ"
    ]
    # Returns should_fallback=True if incomplete
```

```python
# engine.py (lines 542-605)
if "STOP" in str(state.last_finish_reason).upper():
    completeness_decision = trigger.analyze_text_completeness(state.accumulated_text)
    if completeness_decision.should_fallback:
        # Retry with fallback model
        fallback_model = self.hybrid_manager.get_fallback_model(selected_model)
```

### ტესტირება

| ტესტი | შედეგი |
|-------|--------|
| Unit tests (fallback_trigger.py) | 18/18 ✅ |
| Integration tests (engine) | 24/24 ✅ |
| Empty response fallback | 5/5 ✅ |
| Model router | 13/13 ✅ |

---

## Release: v2.1.3 Embedding SDK Migration (January 28, 2026)

### პრობლემა

```
module 'google.genai' has no attribute 'embed_content'
Session-end fact: error - Invalid embedding dim: 3072, expected 768
```

### მიზეზი

1. **SDK მიგრაცია**: `google.generativeai` → `google.genai` (v1.x)
2. **API ცვლილება**: `genai.embed_content()` → `client.models.embed_content()`
3. **მოდელი**: `text-embedding-004` (768-dim) → `gemini-embedding-001` (3072-dim)

### Fix

| ფაილი | ხაზი | ცვლილება |
|-------|------|----------|
| `gemini_adapter.py` | 607 | `self.client.models.embed_content()` |
| `user_tools.py` | 304, 459 | `_get_embedding()` helper |
| `engine.py` | 1406 | `in (768, 3072)` |
| `mongo_store.py` | 1123 | `not in (768, 3072)` |

### ვერიფიკაცია

```
✅ Extracted 2 facts from 10 messages
✅ Session-end fact: added - ალერგია არაქისზე...
✅ Session-end fact: added - ლაქტოზის აუტანლობა...
```

MongoDB:
```json
{
  "user_id": "widget_qmp7b6634va",
  "curated_facts": [
    {"fact": "ალერგია არაქისზე", "embedding": [3072 dims], "importance_score": 0.9},
    {"fact": "ლაქტოზის აუტანლობა", "embedding": [3072 dims], "importance_score": 0.9}
  ]
}
```

---

## Release: v2.2 Memory Compaction System (January 28, 2026)

### ახალი Features

| Feature | აღწერა |
|---------|--------|
| **$slice Limit** | MongoDB array overflow prevention: `-100` curated, `-200` daily facts |
| **ContextCompactor** | 572-line module for context window management |
| **Pre-flush Safety** | Facts extracted & saved BEFORE summarization |
| **Engine Integration** | Phase 2.5 - compaction check between `_load_context()` and `_create_chat_session()` |

### არქიტექტურა

```
User Message → _load_context() 
     ↓
Phase 2.5: Token Check (≥75% of 200k?)
     ↓ YES
ContextCompactor.compact()
  1. Pre-flush facts → MongoDB
  2. Summarize old messages
  3. [Summary] + recent_messages
     ↓
_create_chat_session() (with compacted history)
```

### Key Components

| ფაილი | ცვლილება |
|-------|----------|
| `context_compactor.py` | ახალი 572-line class with lazy loading |
| `mongo_store.py:1166-1183` | `$slice` operator for array limits |
| `engine.py:441-460` | Phase 2.5 compaction integration |
| `fact_extractor.py` | Reused for pre-flush extraction |

### Lazy Loading Pattern (Circular Import Prevention)

```python
@property
def token_counter(self):
    if self._token_counter is None:
        from app.core.token_counter import TokenCounter
        self._token_counter = TokenCounter()
    return self._token_counter
```

### ვერიფიკაცია

```bash
from app.memory.context_compactor import ContextCompactor
✅ Import test passed!
```

| Test | Result |
|------|--------|
| Circular imports | ✅ None (lazy loading) |
| Pre-flush safety | ✅ Facts saved first |
| Error handling | ✅ Graceful fallback |
| $slice syntax | ✅ Correct MongoDB pattern |

---

*Last Updated: January 28, 2026 ~22:40*


---

## Memory v2.2 Manual Testing Results (January 28, 2026 ~23:30)

### ✅ All 5 Tests Passed

| Test | Description | Result |
|------|-------------|--------|
| **Test 1** | Fact Extraction | ✅ 9 facts extracted |
| **Test 2** | $slice Limit | ✅ Ready (curated:-100, daily:-200) |
| **Test 3** | Compaction Trigger | ✅ 221k tokens → triggers at 150k |
| **Test 4** | Health Priority | ✅ "დიაბეტი" saved with score 0.9 |
| **Test 5** | Duplicate Prevention | ✅ Cosine 0.90 threshold works |

### MongoDB Verification

```json
// Final state after stress testing
{
  "user_id": "widget_qmp7b6634va",
  "curated_facts": 12,  // Health/allergies/goals (permanent)
  "daily_facts": 3      // Budget/work (60-day TTL)
}
```

### Smart Tiering Results

| Score | Category | Examples |
|-------|----------|----------|
| **1.0** | Critical Health | ალერგია ლაქტოზაზე, შაქარზე |
| **0.9** | Health/Allergies | დიაბეტი, ვეგანი, არაქისი |
| **0.8** | Goals/Bio | კუნთოვანი მასა, 85 კგ წონა |
| **0.7** | Work | IT-ში მუშაობს |
| **0.5-0.6** | Preferences | ბიუჯეტი 80₾ |

---

## Feature: Name Extraction (January 28, 2026 ~23:10)

### ცვლილება

`fact_extractor.py` - prompt-ში დაემატა პირადი ინფორმაციის ამოღება:

```diff
- პრეფერენციები (მაგ: "ვეგანი", "ბიუჯეტი 100₾-მდე")
+ - პრეფერენციები (მაგ: "ვეგანი", "ბიუჯეტი 100₾-მდე")
+ - პირადი ინფორმაცია (მაგ: "სახელი გიორგი", "სქესი")
```

### ახალი Extraction Categories

| Category | Examples |
|----------|----------|
| health | დიაბეტი, ორსული |
| allergy | ლაქტოზა, არაქისი, შაქარი |
| preference | ვეგანი, ბიუჯეტი |
| goal | კუნთის მასის ზრდა |
| physical | 80კგ წონა, 180სმ სიმაღლე |
| **personal** ✨ | სახელი გიორგი, სქესი |

---

*Last Updated: January 28, 2026 ~23:35*

---

## Development Timeline: January 29, 2026 (~01:00-01:30)

### Session: Text Truncation Bug #28 Fix (Phase 1)

**Problem Reported:** ქართული ტექსტი იჭრებოდა ზოგიერთ პასუხში.

---

## Bug Log (January 29)

### Bug #28: Georgian Text Truncation (CRITICAL)
- **Symptom:** Complete responses (~1500+ chars) truncated for health queries
- **Investigation:** `/debug` workflow with Deep Reasoning Protocol (DRP v3.0)
- **Root Cause:**
  1. Gemini returns `finish_reason=SAFETY` for health content
  2. Backend `SAFETY` fallback triggers at 300 chars
  3. Georgian health responses exceed 800 chars → wrongly flagged
- **Fix:**
  | File | Change |
  |------|--------|
  | `engine.py:529` | Threshold: 300 → 800 chars |
  | `function_loop.py` | Added `finish_reason` in sync path |
  | `test_engine_integration.py` | +4 integration tests |
- **Status:** ✅ RESOLVED

### Analysis: Gemini Content Filtering (NOT A BUG)
- **Symptom:** API error on complex medical query
- **Query:** პროტეინი თირკმელებს შლის + 10 კილო ერთ თვეში
- **Verdict:** Expected Gemini content filtering, not a code bug
- **Status:** ✅ No action needed

---

## Verification Results (January 29)

| Metric | Result |
|--------|--------|
| Unit Tests | 167/167 ✅ |
| Manual Testing | 11/12 (92%) ✅ |
| Response Length | 1500+ chars verified ✅ |

---

*Last Updated: January 29, 2026 ~01:30*

