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

**Root Cause:** AFC (Automatic Function Calling) was enabled by default
**Fix:** `automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)`
- **Status:** ✅ RESOLVED

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

## Search-First Architecture (ANALYZED - Ready for Implementation)

**Injection Point:** `engine.py` → `_enhance_message()` L756-773

**Concept:** Run vector search BEFORE Gemini, inject results into message:
```
User: "მინდა პროტეინი"
     ↓
_enhance_message() runs search_products()
     ↓
Enhanced: "მინდა პროტეინი\n[System: Found 10 products...]"
     ↓
Gemini answers in Round 1 (skips FC!)
```

**Estimated Latency Gain:** 2-4 seconds

**Status:** Ready for implementation if needed.

---

*Last Updated: January 22, 2026 ~23:45*
