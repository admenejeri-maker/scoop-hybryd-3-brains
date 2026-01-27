# 🔥 Gemini 3 Flash Preview - პრობლემები და გადაწყვეტილებები

## Complete Troubleshooting Guide for AI Assistants

*Created: January 27, 2026*
*Project: Scoop.ge AI Chatbot*

---

## 📋 Executive Summary

**gemini-3-flash-preview** მოდელს აქვს რამდენიმე კრიტიკული პრობლემა, რომელიც იწვევს ტექსტის მოჭრას და არასტაბილურ ქცევას. ეს დოკუმენტი აღწერს ყველა აღმოჩენილ პრობლემას და მათ გადაწყვეტილებებს.

---

## 🐛 პრობლემა #1: Safety Filter False Positives (CRITICAL)

### სიმპტომები:
- პასუხი იჭრება შუა სიტყვაზე
- მოკლე პასუხები (50-200 სიმბოლო) ნორმალური 500+ სიმბოლოს ნაცვლად
- ჯანსაღი/სპორტის ნივთები იწვევენ truncation-ს

### მაგალითი:
```
User: "ლაქტოზის აუტანლობა მაქვს, რა ცილა მირჩევთ?"
Response: "გამარჯობა! ლაქტოზის აუტანლობის შემთხვევაში გირჩევთ მცენარეუ..." ← TRUNCATED
Finish Reason: SAFETY
```

### Root Cause:
- `gemini-3-flash-preview` იყენებს `BLOCK_MEDIUM_AND_ABOVE` default safety level
- ეს ძალიან მკაცრია ჯანმრთელობის/კვების რჩევებისთვის
- საკვანძო სიტყვები: "ლაქტოზა", "აუტანლობა", "დიაბეტი", "კანის პრობლემა"

### Diagnostic Code:
```python
# main.py - დაამატეთ ლოგირება
async for chunk in response:
    if chunk.candidates:
        candidate = chunk.candidates[0]
        if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
            logger.warning(f"⚠️ FINISH REASON: {candidate.finish_reason}")
            if str(candidate.finish_reason) == "FinishReason.SAFETY":
                logger.error("🚨 SAFETY FILTER TRIGGERED!")
```

### გადაწყვეტილება:
**მიგრაცია `gemini-2.5-flash` ან `gemini-2.5-pro`-ზე**

| Model | Safety Default |
|-------|----------------|
| gemini-3-flash-preview | BLOCK_MEDIUM ❌ |
| gemini-2.5-flash | **OFF** ✅ |
| gemini-2.5-pro | **OFF** ✅ |

---

## 🐛 პრობლემა #2: Thinking Config Incompatibility

### სიმპტომები:
- API Error 400
- "Invalid parameter" errors
- Suboptimal performance

### Root Cause:
Gemini 3 და Gemini 2.5 იყენებენ სხვადასხვა thinking configuration:

| Model Series | Parameter | Values |
|--------------|-----------|--------|
| **Gemini 3** | `thinking_level` | LOW, HIGH |
| **Gemini 2.5** | `thinking_budget` | 0-24576, -1 (dynamic) |

### არასწორი კოდი (Gemini 3 → 2.5):
```python
# ❌ WRONG - Gemini 2.5 არ ესმის thinking_level
thinking_config=ThinkingConfig(
    thinking_level="HIGH"
)
```

### სწორი კოდი:
```python
# ✅ CORRECT - Gemini 2.5 იყენებს thinking_budget
thinking_config=ThinkingConfig(
    thinking_budget=16384  # HIGH equivalent
)

# ✅ CORRECT - Gemini 3 იყენებს thinking_level
thinking_config=ThinkingConfig(
    thinking_level="HIGH"
)
```

### Mapping Table:
| thinking_level | thinking_budget |
|---------------|-----------------|
| MINIMAL | 0 |
| LOW | 4096 |
| MEDIUM | 8192 |
| HIGH | 16384 |
| Dynamic | -1 |

---

## 🐛 პრობლემა #3: Thought Signatures (Gemini 3 Only)

### სიმპტომები:
- Function calling 400 errors
- Degraded reasoning quality
- Inconsistent responses

### Root Cause:
Gemini 3 მოითხოვს **Thought Signatures** - encrypted reasoning context.

### გადაწყვეტილება:
```python
# Thought signatures must be returned to model in subsequent calls
# Missing signatures = 400 error for function calling
# Required even with thinking_level="low"
```

**ან**: მიგრაცია Gemini 2.5-ზე (არ მოითხოვს thought signatures)

---

## 🐛 პრობლემა #4: Context Cache Minimum Size

### სიმპტომები:
```
400 INVALID_ARGUMENT: Cached content is too small. 
total_token_count=1955, min_total_token_count=2048
```

### Root Cause:
Gemini 2.5 Pro მოითხოვს მინიმუმ **2048 tokens** context caching-სთვის.

### გადაწყვეტილება:
1. გაზარდეთ system instruction/catalog size
2. ან გამორთეთ caching (მუშაობს caching-ის გარეშეც)

```python
# Option 1: Pad system instruction
if token_count < 2048:
    system_instruction += "\n" + "." * (2048 - token_count)

# Option 2: Skip caching
if token_count < 2048:
    logger.warning("Skipping cache - content too small")
    return None
```

---

## 🐛 პრობლემა #5: Preview Model Instability

### სიმპტომები:
- Random API errors
- Inconsistent response quality
- Sudden behavior changes

### Root Cause:
`gemini-3-flash-preview` და `gemini-3-pro-preview` არის **Pre-GA** მოდელები:
- არ არის Production-ready
- შეიძლება შეიცვალოს ნებისმიერ დროს
- Rate limits შეიძლება იყოს უფრო მკაცრი

### გადაწყვეტილება:
**Production-სთვის გამოიყენეთ GA მოდელები:**

| Use Case | Recommended Model |
|----------|-------------------|
| Cost-effective | `gemini-2.5-flash` |
| Balanced | `gemini-2.5-pro` |
| Maximum | `gemini-3-pro-preview` (dev only) |

---

## 📊 Model Comparison Table

| Feature | gemini-3-flash-preview | gemini-2.5-flash | gemini-2.5-pro |
|---------|----------------------|-----------------|----------------|
| Status | Pre-GA ⚠️ | **GA** ✅ | **GA** ✅ |
| Safety | BLOCK_MEDIUM | **OFF** | **OFF** |
| Thinking | thinking_level | thinking_budget | thinking_budget |
| Disable Thinking | ❌ | ✅ | ✅ |
| Thought Signatures | Required | Not needed | Not needed |
| Input Price | $0.50/1M | **$0.10/1M** | $1.25/1M |
| Output Price | $3.00/1M | **$0.40/1M** | $10.00/1M |
| Recommended | Dev/Test | **Production** | Premium |

---

## 🔧 Migration Checklist

### From gemini-3-flash-preview to gemini-2.5-flash/pro:

- [ ] **config.py**: Change `model_name`
- [ ] **config.py**: Update thinking config comments
- [ ] **gemini_adapter.py**: Change `model_name`
- [ ] **main.py** (2x): Change `thinking_level` → `thinking_budget`
- [ ] **evals/judge.py**: Change `model`
- [ ] **Test**: Safety filter queries (lactose, diabetes, etc.)
- [ ] **Verify**: No truncation

### Files to Modify:

```python
# config.py (Line ~39)
model_name: str = "gemini-2.5-pro"  # or gemini-2.5-flash

# config.py (Line ~83)
thinking_budget: int = 16384  # HIGH equivalent

# gemini_adapter.py (Line ~74)
model_name: str = "gemini-2.5-pro"

# main.py (Line ~401, ~431)
thinking_config=ThinkingConfig(
    thinking_budget=settings.thinking_budget
)

# evals/judge.py (Line ~61)
self.model = "gemini-2.5-pro"
```

---

## 🧪 Verification Tests

### Test 1: Safety Filter
```
Query: "ლაქტოზის აუტანლობა მაქვს, რა ცილა მირჩევთ?"
Expected: Full response (300+ chars), no truncation
Check: finish_reason != SAFETY
```

### Test 2: Thinking Works
```
Query: "შემომთავაზე რთული აუზროვნება საჭირო საკითხი"
Expected: Thoughtful, detailed response
Check: include_thoughts shows thinking process
```

### Test 3: Function Calling
```
Query: "მინდა ციტრუსის არომატის გელი"
Expected: Products returned from MongoDB
Check: No 400 errors, products displayed
```

---

## 📝 Diagnostic Logging Template

დაამატეთ ეს main.py-ში debugging-სთვის:

```python
import logging
logger = logging.getLogger(__name__)

# In streaming loop:
chunk_count = 0
total_text = ""

async for chunk in response:
    chunk_count += 1
    if chunk.text:
        total_text += chunk.text
    
    if chunk.candidates:
        candidate = chunk.candidates[0]
        if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
            finish_reason = str(candidate.finish_reason)
            logger.info(f"📊 Stream Stats: chunks={chunk_count}, chars={len(total_text)}, finish={finish_reason}")
            
            if "SAFETY" in finish_reason:
                logger.error(f"🚨 SAFETY TRUNCATION! Only {len(total_text)} chars delivered")
            elif "MAX_TOKENS" in finish_reason:
                logger.warning(f"⚠️ MAX_TOKENS reached, increase max_output_tokens")
```

---

## 🎯 Quick Decision Tree

```
პრობლემა: ტექსტი იჭრება?
├── Check finish_reason in logs
│   ├── SAFETY → Migrate to gemini-2.5-flash/pro
│   ├── MAX_TOKENS → Increase max_output_tokens
│   ├── RECITATION → Rephrase query
│   └── STOP → Normal (no issue)
│
პრობლემა: API 400 Error?
├── Check ThinkingConfig
│   ├── Gemini 3 → Use thinking_level
│   └── Gemini 2.5 → Use thinking_budget
│
პრობლემა: Function calling fails?
├── Gemini 3 → Check thought signatures
└── Gemini 2.5 → Should work without signatures
```

---

## 📚 References

- [Gemini Thinking Docs](https://ai.google.dev/gemini-api/docs/thinking)
- [Gemini 3 Developer Guide](https://ai.google.dev/gemini-api/docs/gemini-3)
- [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing)

---

*Last Updated: January 27, 2026 ~02:00*
*Author: AI Assistant (Claude)*
