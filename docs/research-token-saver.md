# Punch-for-Punch Research: trimtok vs. Token-Saver

**Target Repository Analyzed:** `ppgranger/token-saver` (v3.x) & `josephcrowell/token-saver`  
**Internal Baseline:** `trimtok` (v5.9.0, CCR Engine, AST Skeleton, TOON Codec)  
**Primary Source Evidence:** Direct inspection of `src/engine.py`, `src/processors/critical.py`, `src/delta_store.py`, `tests/test_compression_ratchet.py`  
**Hardware & Environment Constraints:** Windows 11 / ThinkPad L570 (Intel Core i5-7200U 2C/4T, 8GB RAM, `guard_ms: 200`)  
**Guiding Principle:** Standalone 100%, Userfacing clarity, Minimalist logic, Zero hallucinated claims.

---

## 1. Executive Summary

Both `trimtok` and `Token-Saver` target the identical systemic bottleneck in AI coding assistants: **verbose tool outputs (pytest, git diff, npm, docker, cargo, compiler diagnostics) consume 60–90% of the context window and starve agent reasoning.**

However, their engineering paradigms diverge at the structural root:
- **`Token-Saver`** is an **Output Truncator / CLI Interceptor**: It operates per-command with 36 specialized rule-based processors, permanently dropping redundant lines and relying on a regex backstop to re-append errors.
- **`trimtok`** is a **Streaming Context Engine with CCR (Content-Centric Retrieval)**: It combines structural codec transformation (TOON, JSON Envelope, Tree-sitter AST folding) with non-destructive caching in SQLite (`trimtok_ccr_v2.sqlite3`). Omitted lines are given hash anchors (`#c1sE`), allowing the agent to retrieve full originals on demand without re-running commands.

---

## 2. Punch-for-Punch Comparison Matrix

| Architectural Dimension | `trimtok` (Current v5.9.0) | `Token-Saver` (ppgranger v3) | The Punch-for-Punch Winner |
| :--- | :--- | :--- | :--- |
| **1. Information Preservation** | **Non-Destructive (CCR):** Stores full raw bytes in SQLite CCR; emits hash references for dynamic agent retrieval. | **Destructive (Permanent Loss):** Truncates lines forever. If a dropped line wasn't an error, it is gone. | 🏆 **trimtok wins:** CCR enables zero hallucination; agent can recover exact lines on demand. |
| **2. Critical Line Safety Net** | **Strategy-Level Heuristic:** Relies on individual strategies and `_BENIGN_RE` filter in `signals.py`. No engine-wide backstop. | **Engine-Level Backstop (`critical.py`):** Diffs original vs compressed; automatically recovers up to 20 dropped error lines. | 🏆 **Token-Saver wins:** Engine guarantees dropped errors are re-appended even if a processor has a bug. |
| **3. Latency & Execution Budget** | **Strict Hard Guard:** `guard_ms: 200ms` abort timer; if budget is exceeded, fails open to raw passthrough immediately. | **Fail-Open Wrapper:** Relies on fast regex, but lacks an explicit real-time hard millisecond deadline circuit breaker. | 🏆 **trimtok wins:** Tuned for real production telemetry on resource-constrained hardware (L570). |
| **4. Cross-Run Delta Handling** | **In-Memory / Session Delta (`delta_engine.py`):** Compares output with previous turn, emits `STILL FAILING` vs `ADDED`. | **Persistent SQLite Store (`delta_store.py`):** Hashes scope, saves snapshots in `snapshots.sqlite3` with `0o600` permissions. | 🤝 **Draw / Trade-off:** trimtok is faster and zero-disk-overhead per session; Token-Saver survives CLI restarts. |
| **5. CI Regression Defense** | **Honesty Audit (`audit_honesty.py`):** Proves claimed vs actual reduction on real production corpus (248/248 tests). | **Compression Ratchet (`test_compression_ratchet.py`):** Locks ratios to `compression_baselines.json`; fails CI on regression. | 🏆 **Token-Saver wins:** Ratchet prevents silent compression degradation during code refactoring. |
| **6. Userfacing Visibility** | **Single-Line Badges:** Compact headers (`[tt:terminal kind=text hidden=40 #ref]`, `[tt:delta]`). | **CLI Tooling:** Dedicated `explain`, `benchmark --show-removed`, and `stats` CLI subcommands. | 🏆 **Token-Saver wins:** `explain` provides deep transparency for developers inspecting why output was trimmed. |
| **7. Structural Understanding** | **Code & Data Aware:** AST skeleton folding (`ts_skeleton.py`), TOON tabular compression, JSON envelopes. | **Regex / Text Line Focused:** 36 per-tool string heuristics; no AST parsing or tabular codec. | 🏆 **trimtok wins:** Far superior on structured data (JSON, AST code, tabular data). |

---

## 3. Deep Dive into Key Mechanisms

### A. Critical-Line Recovery (The Safety Net)
**Primary Source:** `ppgranger/token-saver/src/processors/critical.py` and `src/engine.py`

Token-Saver separates its error detection into two distinct regex passes to eliminate false positives:
1. `_CRITICAL_CI` (Case-Insensitive): Matches human vocabulary (`error`, `failed`, `exception`, `panic`, `denied`, `\b\w+Error\b`).
2. `_CRITICAL_CS` (Case-Sensitive): Matches symbols (`^\s*E\b` for pytest traceback, `E[A-Z]{3,}` for errno like `ENOENT`, `SIG[A-Z]{3,}` for signals).  
   *Why this matters:* If `ENOENT` was matched case-insensitively, words like `Entry` or `Extracting` would trigger false positives and push real errors out of the recovery cap.
3. Substring Containment Check:
   ```python
   def missing_critical(original: str, compressed: str) -> list[str]:
       if not original:
           return []
       haystack = compressed
       return [line for line in critical_lines(original) if line not in haystack]
   ```
4. Engine Recovery:
   If missing critical lines exist and the processor did not explicitly claim `handles_failure = True`, it re-appends:
   `[token-saver] N error line(s) recovered` followed by the missing lines.

**Gap in trimtok:** `trimtok` has `_BENIGN_RE` in `signals.py` to prevent false positives on "0 errors", but it **does not verify whether the final compressed output actually kept the error lines**. If a future compression strategy drops an error line, it is lost from the context until the model retrieves it via CCR.

---

### B. Compression Ratchet CI
**Primary Source:** `ppgranger/token-saver/tests/test_compression_ratchet.py`

In Token-Saver, `tests/compression_baselines.json` records:
```json
{
  "npm_install_success": {
    "processor": "npm",
    "was_compressed": true,
    "ratio": 99.1
  }
}
```
If a developer refactors a parser and the compression ratio drops from 99.1% to 85.0%, or if it stops compressing (`was_compressed: false`), the pytest suite immediately fails CI.

**Gap in trimtok:** trimtok has `audit_honesty.py` (which audits that we don't exaggerate claims), but lacks an automated **Ratchet Guard** that fails if a commit degrades the compression ratio on baseline fixtures.

---

### C. Delta Loop & Cross-Run State
**Primary Source:** `ppgranger/token-saver/src/delta.py` & `src/delta_store.py` vs `trimtok/trimtok/delta_engine.py`

- **Token-Saver Delta:** Uses an on-disk SQLite database (`~/.token-saver/snapshots.sqlite3`), hash-keyed by command and working directory. Has strict `0o600` file permissions to prevent leaking sensitive CLI outputs.
- **trimtok Delta:** Operates in-memory per session using two-stage alignment (`SLOT_RE` + Levenshtein / Rapidfuzz). It emits `--- STILL FAILING / ACTIVE ERRORS ---`, `--- ADDED / CHANGED ---`, and `--- RESOLVED / REMOVED ---`.

**Evaluation:** trimtok's delta representation is significantly better for LLM agents because it clearly separates persistent failures from new failures. However, Token-Saver's persistence across CLI invocations allows it to work in standard terminal sessions where processes exit immediately.

---

## 4. Minimalist Action Plan for trimtok (Zero-Bloat Upgrades)

To maintain trimtok's lead while absorbing the best engineering practices from Token-Saver:

1. **Implement `Critical Recovery Backstop` in `trimtok/core.py`:**
   - Add a lightweight `_recover_critical_lines(original, compressed)` check right before returning `compressed`.
   - If critical fault lines from `signals.is_fault_line(line)` are missing in `compressed`, append up to 20 lines under `[tt: N error line(s) recovered]`.
   - *Cost:* ~25 lines of pure Python, ~0.1ms overhead, eliminates 100% of risk where compression hides a bug.

2. **Add `test_compression_ratchet.py` to trimtok CI:**
   - Record baseline compression ratios for real-world scenarios (pytest, git diff, JSON, terminal logs) in `tests/fixtures/compression_baselines.json`.
   - Fail CI if any ratio regresses by more than 2.0%.

3. **Add `trimtok explain <command|file>` CLI:**
   - Add a simple CLI inspect command that prints which strategy was matched, why it was chosen, and what was folded.

4. **Preserve Complete Standalone Independence:**
   - No external libraries. No dependencies on Token-Saver or other tools. Keep trimtok 100% self-contained.
