# Scoop AI v3.0 Architecture

## Overview

Scoop AI v3.0 introduces a **Hybrid 3-Tier Inference System** with automatic SAFETY fallback. This architecture provides production-grade reliability with intelligent model routing, circuit breaking, and mid-stream recovery.

---

## Design Principles

1. **Hybrid Inference** - Primary model with automatic fallback cascade
2. **Mid-Stream SAFETY Recovery** - Detect and recover from Gemini SAFETY blocks
3. **Circuit Breaking** - Protect against cascading failures
4. **Token-Aware Routing** - Automatic model selection based on context size
5. **Fail-Fast with Graceful Degradation** - Clear error states with recovery options

---

## Component Hierarchy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         v3.0 COMPONENT HIERARCHY                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  app/                                                                        │
│  ├── core/                          # Core engine components                 │
│  │   ├── engine.py                  # ConversationEngine (orchestrator)     │
│  │   ├── function_loop.py           # FunctionCallingLoop + finish_reason   │
│  │   ├── types.py                   # RoundOutput, LoopState with finish    │
│  │   ├── response_buffer.py         # ResponseBuffer                        │
│  │   ├── thinking_manager.py        # ThinkingManager                       │
│  │   ├── tool_executor.py           # ToolExecutor                          │
│  │   │                                                                       │
│  │   ├── hybrid_manager.py          # 🆕 HybridInferenceManager             │
│  │   ├── circuit_breaker.py         # 🆕 CircuitBreaker                     │
│  │   ├── token_counter.py           # 🆕 TokenCounter                       │
│  │   ├── model_router.py            # 🆕 ModelRouter                        │
│  │   └── fallback_trigger.py        # 🆕 FallbackTrigger                    │
│  │                                                                           │
│  ├── adapters/                      # External service adapters              │
│  │   ├── gemini_adapter.py          # Gemini SDK wrapper                    │
│  │   └── mongo_adapter.py           # MongoDB operations                    │
│  │                                                                           │
│  └── tools/                         # Tool execution                         │
│      └── user_tools.py              # Product search, profile tools         │
│                                                                              │
│  main.py                            # Thin controller                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Hybrid Inference Architecture

### 3-Tier Model Cascade

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    3-TIER MODEL FALLBACK CASCADE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  TIER 1: PRIMARY                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                     gemini-3-flash-preview                              ││
│  │  • Fastest latency (~500ms)                                             ││
│  │  • Cost: Low                                                            ││
│  │  • Use case: Normal queries, product search                             ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                              │                                               │
│                    ┌─────────┴─────────┐                                    │
│                    │ SAFETY | ERROR    │                                    │
│                    └─────────┬─────────┘                                    │
│                              ▼                                               │
│  TIER 2: EXTENDED THINKING                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                        gemini-2.5-pro                                   ││
│  │  • Deep reasoning capability                                            ││
│  │  • 16K thinking budget                                                  ││
│  │  • Use case: Complex queries, SAFETY recovery                           ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                              │                                               │
│                    ┌─────────┴─────────┐                                    │
│                    │ SAFETY | ERROR    │                                    │
│                    └─────────┬─────────┘                                    │
│                              ▼                                               │
│  TIER 3: FALLBACK                                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                      gemini-2.5-flash                                   ││
│  │  • Most permissive safety settings                                      ││
│  │  • 24K thinking budget (HIGH)                                           ││
│  │  • Use case: Last resort fallback                                       ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components (v3.0 New)

### 1. HybridInferenceManager (`app/core/hybrid_manager.py`)

Orchestrates the hybrid inference architecture.

```python
class HybridInferenceManager:
    """
    Unified interface for hybrid inference.
    
    Coordinates:
        - CircuitBreaker: API stability protection
        - TokenCounter: Context window management  
        - ModelRouter: Model selection logic
        - FallbackTrigger: Error detection and fallback decisions
    """
```

**Architecture Flow:**
```
Request → TokenCounter → ModelRouter → Primary Model
                              ↓
                         CircuitBreaker.is_allowed?
                              ↓
                         SUCCESS → Update state
                         FAILURE → FallbackTrigger.analyze()
                              ↓
                         Retry? → Primary Model (retry)
                         Fallback? → Next Tier Model
```

### 2. CircuitBreaker (`app/core/circuit_breaker.py`)

Protects against cascading API failures.

```python
class CircuitBreaker:
    """
    States:
        CLOSED  - Normal operation, all requests allowed
        OPEN    - Failure threshold exceeded, all requests blocked
        HALF    - Recovery mode, limited requests allowed
    
    Transitions:
        CLOSED → OPEN:  5 consecutive failures
        OPEN → HALF:    60 seconds recovery period
        HALF → CLOSED:  1 successful request
        HALF → OPEN:    1 failed request
    """
```

### 3. TokenCounter (`app/core/token_counter.py`)

Context window management with model-specific limits.

```python
class TokenCounter:
    """
    Token limits per model:
        gemini-3-flash-preview: 30,000 tokens
        gemini-2.5-pro:         128,000 tokens
        gemini-2.5-flash:       128,000 tokens
    
    Features:
        - Accurate Georgian text estimation (1 char ≈ 0.8 tokens)
        - History pruning suggestions when approaching limit
    """
```

### 4. ModelRouter (`app/core/model_router.py`)

Intelligent model selection.

```python
class ModelRouter:
    """
    Routing priorities:
        1. Check CircuitBreaker health for primary
        2. Check token count vs model limits
        3. Check recent failure patterns
        
    Returns:
        RoutingDecision(model, reason, can_retry, fallback_options)
    """
```

### 5. FallbackTrigger (`app/core/fallback_trigger.py`)

Error detection and recovery decisions.

```python
class FallbackTrigger:
    """
    Analyzes response issues:
        - SAFETY block (finish_reason=SAFETY)
        - Empty response (text < 300 chars)
        - API errors (429, 500, timeout)
        
    Returns:
        FallbackDecision(should_fallback, reason, next_model)
    """
```

---

## SAFETY Fallback Mechanism

### Detection Logic

```python
# In engine.py - after stream completes
if (last_finish_reason == FinishReason.SAFETY and 
    len(accumulated_text.strip()) < 300):
    # Gemini blocked response mid-stream
    # Trigger fallback to next tier
```

### Georgian Text Threshold

**Why 300 characters?**
- Georgian text uses ~3 bytes per character
- 300 chars = ~1-2 sentences minimum
- Below this threshold = incomplete response due to safety block
- Above this = legitimate short response (allowed)

### Response Flow with SAFETY Fallback

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SAFETY FALLBACK FLOW                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. User Message: "მინდა წონის კლება + გეინერი"                              │
│                                                                              │
│  2. gemini-3-flash-preview responds:                                         │
│     ┌─────────────────────────────────────────────────────────┐             │
│     │ text: "გამარჯობა..." (60 chars)                         │             │
│     │ finish_reason: SAFETY ← Blocked!                        │             │
│     └─────────────────────────────────────────────────────────┘             │
│                                                                              │
│  3. Detection:                                                               │
│     ┌─────────────────────────────────────────────────────────┐             │
│     │ finish_reason == SAFETY? ✓                              │             │
│     │ text.strip() < 300? ✓ (60 < 300)                        │             │
│     │ → TRIGGER FALLBACK                                      │             │
│     └─────────────────────────────────────────────────────────┘             │
│                                                                              │
│  4. gemini-2.5-pro (extended thinking) responds:                             │
│     ┌─────────────────────────────────────────────────────────┐             │
│     │ text: "გიორგი, მესმის რომ გსურს..." (2500 chars)        │             │
│     │ finish_reason: STOP ← Complete!                         │             │
│     │ products: [Applied Nutrition Plant Protein]             │             │
│     └─────────────────────────────────────────────────────────┘             │
│                                                                              │
│  5. User sees: Complete educational response about goals                     │
│     (No indication that fallback occurred)                                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow (v3.0)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REQUEST FLOW (v3.0)                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. REQUEST ARRIVES                                                          │
│     POST /chat/stream { user_id, message }                                   │
│                              │                                               │
│                              ▼                                               │
│  2. HYBRID ROUTING                                                           │
│     ┌─────────────────────────────────────────────────────────┐             │
│     │ HybridInferenceManager.get_routing_decision()           │             │
│     │   → TokenCounter.count_tokens(history + message)        │             │
│     │   → CircuitBreaker.is_allowed(primary)?                 │             │
│     │   → ModelRouter.select(token_count, health)             │             │
│     │   → Returns: gemini-3-flash-preview                     │             │
│     └─────────────────────────────────────────────────────────┘             │
│                              │                                               │
│                              ▼                                               │
│  3. FUNCTION CALLING LOOP                                                    │
│     ┌─────────────────────────────────────────────────────────┐             │
│     │ FunctionCallingLoop.execute_streaming()                 │             │
│     │   → Streams response chunks                             │             │
│     │   → Tracks finish_reason per chunk                      │             │
│     │   → Returns RoundOutput with last_finish_reason         │             │
│     └─────────────────────────────────────────────────────────┘             │
│                              │                                               │
│                              ▼                                               │
│  4. SAFETY CHECK                                                             │
│     ┌─────────────────────────────────────────────────────────┐             │
│     │ if finish_reason == SAFETY and text < 300:              │             │
│     │   → Log: "⚠️ SAFETY detected, attempting fallback"       │             │
│     │   → FallbackTrigger.get_fallback() → gemini-2.5-pro     │             │
│     │   → Retry with fallback model                           │             │
│     └─────────────────────────────────────────────────────────┘             │
│                              │                                               │
│                              ▼                                               │
│  5. SSE STREAMING                                                            │
│     yield SSEEvent(type="text", content="...")                               │
│     yield SSEEvent(type="products", content=[...])                           │
│     yield SSEEvent(type="done")                                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Configuration

### Model Configuration (config.py)

```python
# Primary model (fastest)
GEMINI_MODEL = "gemini-3-flash-preview"

# Extended thinking model
GEMINI_EXTENDED_MODEL = "gemini-2.5-pro"

# Fallback model (most permissive)
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"

# Thinking budgets
EXTENDED_THINKING_BUDGET = 16384  # 2.5-pro
FALLBACK_THINKING_BUDGET = 24576  # 2.5-flash (HIGH)
```

### HybridConfig Defaults

```python
@dataclass
class HybridConfig:
    primary_model: str = "gemini-3-flash-preview"
    fallback_model: str = "gemini-2.5-flash"
    extended_model: str = "gemini-2.5-pro"
    
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: float = 60.0
    
    token_threshold_ratio: float = 0.85
    fallback_text_threshold: int = 300  # Georgian chars
```

---

## Types (v3.0 Additions)

### RoundOutput (updated)

```python
@dataclass
class RoundOutput:
    """Output from a single round of function calling."""
    result: RoundResult
    content: Content
    text: str
    function_calls: List[Any]
    finish_reason: Optional[FinishReason] = None  # 🆕
```

### LoopState (updated)

```python
@dataclass
class LoopState:
    """Accumulated state across all rounds."""
    messages: List[Content]
    all_products: List[dict]
    executed_queries: Set[str]
    all_searched_products: List[dict]
    last_finish_reason: Optional[FinishReason] = None  # 🆕
```

---

## Logging

### Key Log Messages

```bash
# Routing decision
🔀 Routed to gemini-3-flash-preview: reason=primary_healthy, tokens=2258

# SAFETY detection
🏁 finish_reason: FinishReason.SAFETY (79 chars)
⚠️ SAFETY detected with only 79 chars, attempting fallback retry...

# Fallback execution
📥 Fallback for 'gemini-3-flash-preview' → 'gemini-2.5-pro'
🔄 Retrying with fallback model: gemini-2.5-pro

# Fallback success
✅ Fallback complete: 2549 chars, finish_reason=FinishReason.STOP
```

---

## Metrics (v2.0 → v3.0)

| Metric | v2.0 | v3.0 | Change |
|--------|------|------|--------|
| Model tiers | 1 | 3 | **+200%** |
| SAFETY recovery | Manual | Automatic | **+∞** |
| Circuit breaking | None | Full | **New** |
| Token tracking | None | Per-request | **New** |
| New components | 0 | 5 | **New** |
| Test coverage | 186 | 210+ | **+13%** |

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run hybrid inference tests specifically
pytest tests/test_hybrid_manager.py tests/test_circuit_breaker.py \
       tests/test_model_router.py tests/test_fallback_trigger.py \
       tests/test_token_counter.py -v

# Run integration tests
pytest tests/core/test_engine_integration.py -v
```

---

## Removed/Updated Components (v2.0 → v3.0)

| Component | v2.0 | v3.0 |
|-----------|------|------|
| Single model | ✓ | 3-tier cascade |
| Static SAFETY handling | ✓ | Dynamic fallback |
| No circuit breaking | ✓ | CircuitBreaker |
| No token tracking | ✓ | TokenCounter |
| finish_reason ignored | ✓ | Tracked in types |

---

## See Also

- [CONTEXT.md](./CONTEXT.md) - Full development history
- [GEMINI_TROUBLESHOOTING.md](./docs/GEMINI_TROUBLESHOOTING.md) - Gemini API issues
- [README.md](./README.md) - Quick start guide
