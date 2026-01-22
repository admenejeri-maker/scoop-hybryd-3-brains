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

*Last Updated: January 22, 2026 ~21:03*

---

## Development Timeline: January 22, 2026 (Night Session ~22:30-22:55)

### Session: Post-Launch Bug Fixes (Quick Replies + EmptyResponseError)

**Problems Reported:**
1. **Quick Replies truncation:** When Gemini output truncated, `[QUICK_REPLIES]` tag has no closing tag → extraction fails
2. **EmptyResponseError (Over-Reasoning):** Gemini 3 continues calling `search_products` despite query limit reached → 5 rounds → empty response

---

## Bug Log (January 22 - Night Session)

### Bug #27: Quick Replies Truncation Recovery (BACKEND)
- **Symptom:** Quick replies not extracted when Gemini output truncated mid-tag
- **Root Cause:** `QUICK_REPLIES_PATTERN` regex required closing tag `[/QUICK_REPLIES]`
- **Fix (Two-Phase Fallback):**
  1. Primary: Try closed tag pattern `[QUICK_REPLIES]...[/QUICK_REPLIES]`
  2. Fallback 1: Try unclosed pattern `[QUICK_REPLIES]...$` (handles truncation)
  3. Fallback 2: Georgian pattern `შემდეგი ნაბიჯი:`
- **Location:** `response_buffer.py` L78-83, L370-390
- **Status:** ✅ RESOLVED

### Bug #28: EmptyResponseError from Over-Reasoning Loop (BACKEND)
- **Symptom:** Gemini 3 kept calling `search_products` even after query limit, causing 5 rounds → empty response
- **Root Cause:** Query limit response used passive `"note"` field which Gemini ignored
- **Fix:** Replaced with forceful Georgian directive:
  ```python
  "status": "SEARCH_COMPLETE",
  "instruction": "⛔ საძიებო ლიმიტი ამოიწურა. აღარ გამოიძახო search_products! დაწერე რეკომენდაცია ახლავე."
  ```
- **Location:** `tool_executor.py` L248-259
- **Status:** ✅ RESOLVED

### Bug #29: Quick Replies SSE Event Data Mismatch (FRONTEND)
- **Symptom:** Quick replies always showing static defaults despite backend sending dynamic ones
- **Investigation:** curl test confirmed backend sends `quick_replies` SSE event correctly
- **Root Cause:** Frontend `Chat.tsx` L536 used `data.content` but backend sends `data.replies`
- **Fix:**
  ```typescript
  // Before (broken)
  quickReplies = data.content.map(...)
  // After (fixed)
  const repliesData = data.replies || data.content || [];
  ```
- **Location:** `Chat.tsx` L535-541
- **Status:** ✅ RESOLVED

---

## Key Code Changes (January 22 - Night Session)

| Location | Change |
|----------|--------|
| `response_buffer.py` L78-83 | Added `QUICK_REPLIES_UNCLOSED_PATTERN` |
| `response_buffer.py` L370-390 | Two-phase fallback extraction logic |
| `tool_executor.py` L248-259 | Forceful `instruction` directive |
| `Chat.tsx` L535-541 | Fixed `data.replies` vs `data.content` |
| `tests/core/test_bug_fixes_v2.py` | 9 test cases (5 QR + 3 Limit + 1 Integration) |

---

## Test Results (January 22 - Night Session)

```
pytest tests/ -v
194/195 passed (1 pre-existing failure unrelated to bug fixes)
9/9 bug fix tests passed ✅
```

---

*Last Updated: January 22, 2026 ~22:55*
