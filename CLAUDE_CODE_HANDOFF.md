# Claude Code Handoff - Scoop AI Backend Issues

**Date:** January 22, 2026 ~22:20
**Status:** Analysis Required (NO CODE CHANGES YET)
**Priority:** High

---

## 🎯 Two Critical Issues Need Investigation

### Issue #1: Quick Replies Not Working
### Issue #2: EmptyResponseError on Complex Queries

---

# Issue #1: Quick Replies

## Problem Description
Quick replies should return contextual questions (e.g., "როგორ მივიღო სწორად?"), but either:
- Return static placeholders ("გაყიდვა 1", "გაყიდვა 2")
- Return empty (0 items parsed)

## Debug Findings

### Finding 1: Gemini Truncation
```log
🔍 Buffer text before QR parse:
...[QUICK_REPLIES]
- რომელი არომატია ყველაზე გემრიელი?
- რამდენი გრამი ცილა მჭირდება დღეში?
(NO CLOSING TAG!)

🎯 Quick replies parsed: 0 items
```

**Cause:** Gemini generates `[QUICK_REPLIES]` but truncates before `[/QUICK_REPLIES]`

### Finding 2: Regex Requires Closing Tag
```python
# response_buffer.py L366
QUICK_REPLIES_PATTERN = re.compile(
    r'\[QUICK_REPLIES\](.*?)\[/QUICK_REPLIES\]',  # ← Requires both tags!
    re.DOTALL | re.IGNORECASE
)
```

### Finding 3: Old Repo Comparison
Old repo `scoop-generative-ai-sdk-28-04` had `parse_quick_replies()` in main.py (~80KB monolith).
New v2.0 has modular `ResponseBuffer` class with thread-safety, but **missing unclosed tag fallback**.

## Files to Analyze

| File | Location | Purpose |
|------|----------|---------|
| `response_buffer.py` | `app/core/response_buffer.py` | Quick replies parsing logic |
| `engine.py` | `app/core/engine.py` | Buffer usage, SSE event sending |
| `system_prompt_lean.py` | `prompts/system_prompt_lean.py` | Prompt instructions for QR format |

## Proposed Fix (Needs Verification)

Add fallback regex for unclosed `[QUICK_REPLIES]` tag:
```python
# After primary pattern fails:
unclosed_match = re.search(r'\[QUICK_REPLIES\](.*?)$', self._text, re.DOTALL | re.IGNORECASE)
```

**⚠️ WARNING:** Previous fix attempt was reverted due to cascading issues. Need careful testing.

---

# Issue #2: EmptyResponseError on Complex Queries

## Problem Description
Complex queries fail with:
```
ERROR - Empty response in stream: Max streaming rounds with no text
```

## Debug Findings

### Trace from Real Request
```log
🔄 Streaming round 1/5: search_products('plant protein creatine') → 6 products
🔄 Streaming round 2/5: search_products('vegan protein') → 0 products
🔄 Streaming round 3/5: search_products('creatine monohydrate') → 10 products ✅ (16 total)
🔄 Streaming round 4/5: search_products('plant protein') → ⚠️ Query limit reached (3)
🔄 Streaming round 5/5: search_products('BioTech Vegan') → ⚠️ Query limit reached (3)
❌ ERROR - Max streaming rounds with no text
```

### Root Cause Analysis
1. **Gemini Behavior:** Uses all 5 rounds for function calls, doesn't generate text
2. **Query Limit (3):** After 3 searches, `tool_executor` returns limit message
3. **Gemini Ignores Limit:** Continues calling `search_products` instead of writing response
4. **Result:** 16 products found, 0 text generated

### Stats at Failure
| Metric | Value |
|--------|-------|
| Rounds used | 5/5 |
| Queries made | 3/3 (limit) |
| Products found | 16 |
| Text generated | ❌ **0** |

## Files to Analyze

| File | Location | Purpose |
|------|----------|---------|
| `tool_executor.py` | `app/core/tool_executor.py` | Query limit logic & message |
| `function_loop.py` | `app/core/function_loop.py` | Round management |
| `engine.py` | `app/core/engine.py` | EmptyResponseError handling |
| `config.py` | `config.py` | MAX_FUNCTION_CALLS setting |

## Proposed Solutions (Analysis Needed)

### Option A: Force Text After Limit
Modify query limit message to force text generation:
```python
# In tool_executor.py when limit reached:
return "STOP calling tools. Write your response NOW with the 16 products you already found."
```
- **Pro:** Direct approach
- **Con:** Gemini might ignore

### Option B: Increase Rounds
Change `MAX_FUNCTION_CALLS=7`
- **Pro:** More headroom
- **Con:** Escalation risk, latency increase

### Option C: Reduce Searches via Prompt
Add to system prompt: "Maximum 2 search calls per request"
- **Pro:** Efficiency
- **Con:** Quality tradeoff on complex queries

### Option D: Fallback Text on Error
Add fallback in `engine.py` EmptyResponseError handler:
```python
except EmptyResponseError:
    # Generate basic response with collected products
    yield SSEEvent("text", {"content": generate_fallback(state.all_products)})
```
- **Pro:** Safety net
- **Con:** Masks underlying problem

### Recommended: A + D Combination
Defense in Depth - prevention (A) + safety net (D)

---

## Current State

| Component | Status |
|-----------|--------|
| Codebase | Reverted to `cb0e9f0` (stable) |
| GitHub | `5494e40` (revert commit) |
| Quick Replies | ❌ Not working |
| Complex Queries | ❌ EmptyResponseError |

## Git History
```
5494e40 (HEAD) Revert "fix: Dynamic quick replies + unclosed tag fallback"
991a63a fix: Dynamic quick replies + unclosed tag fallback (REVERTED)
592a96c docs: Add January 22 late evening session
cb0e9f0 fix: Session amnesia, NoneType crash ← CURRENT STABLE
```

---

## Instructions for Next Session

1. **Read this handoff first**
2. **Start with Issue #2 (EmptyResponseError)** - more impactful
3. **Analyze existing limit handling in tool_executor.py**
4. **Propose implementation plan before coding**
5. **Test with complex query:** "vegan პროტეინი და კრეატინი 150 ლარამდე"
6. **Then tackle Issue #1 (Quick Replies)**

---

## Test Commands

```bash
# Test complex query (should fail currently)
curl -X POST http://localhost:8080/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "vegan პროტეინი და კრეატინი 150 ლარამდე", "user_id": "test", "session_id": "test"}'

# Check logs
tail -f backend logs | grep -E "(ERROR|Quick replies|Query limit)"
```

---

*Handoff created: January 22, 2026 ~22:20*
