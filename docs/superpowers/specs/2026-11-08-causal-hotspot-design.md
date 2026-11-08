# Trimtok Causal Hotspots: Terminal Error Signal to Read-File Focus Protection

**Status:** Proposed  
**Author:** Mika (Strategic Consigliere) & พี่โชค  
**Target:** trimtok (v5.8.1 roadmap)  
**Date:** 2026-11-08  

---

## 1. Intent & The Why

In autonomous coding agent workflows (e.g., Hermes Agent, Claude Code, Antigravity), the dominant debugging loop is:
1. `terminal`: Execute tests or compile commands (e.g., `pytest`, `cargo test`, `python script.py`).
2. Failure occurs: Output emits an error traceback pointing to a specific file and line (e.g., `File "trimtok/core.py", line 540, in compress_terminal`).
3. `read_file`: The agent reads the failing file (e.g., `read_file("trimtok/core.py")`) to diagnose and construct a patch.

**The Multi-Turn Penalty Trap:**
When a target file exceeds the cold-read skeletonization threshold (> 14KB/20KB), Trimtok's AST code skeletonizers (`smart_skeletonize_code`, `ts_skeleton.py`) fold oversized function implementations (> 8-15 lines) into line-numbered stubs (`[tt:body L480-L616 ...]`) backed by CCR. If the failing line (e.g. line 540) falls inside a folded function, the agent is forced to spend an extra turn calling `trimtok_retrieve` or `read_file(offset=..., limit=...)`. Each extra turn incurs full context re-transmission latency and token cost.

By stashing recent failure coordinates from terminal executions into a lightweight in-memory session ring buffer, Trimtok can automatically protect the exact failing function during the subsequent `read_file` call (keeping it 100% verbatim with real line numbers) while folding all unrelated functions elsewhere in the file.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Automatic Failure Hotspot Detection:** Fast regex extraction of `(file_path, line_no)` from terminal command failures (Tracebacks, Pytest failures, compiler errors).
- **Session-Scoped Hotspot Ring Buffer:** Pure in-memory LRU/FIFO buffer (up to 10 files per session) inside `_seen(session_id)`. Zero disk I/O, zero cross-process locking overhead.
- **Surgical AST Protection:** Extend `smart_skeletonize_code` and `skeletonize_code_full` to accept `focus_lines`. If a function's line span contains any focus line, it is preserved 100% verbatim.
- **Sub-millisecond Performance:** Extraction and lookup overhead must execute in < 0.2ms, comfortably fitting within the 200ms fail-open guard budget.

### Non-Goals
- **No Global Signal Bus Daemon:** No background services, IPC daemons, or heavy message queues. Everything resides in existing process-local session state.
- **No Blanket File Passthrough:** Do not revert the entire file to uncompressed text. Functions not containing the hotspot remain folded to conserve context tokens.
- **No Multi-File Deep Call Graph Analysis:** Avoid static analysis or AST walking across external imported modules; only direct failure sites are tracked.

### Anti-Goals
- Altering or degrading existing 246/246 real corpus regression checks or line prefix integrity (`LINE|`).
- Leaking hotspots across unrelated sessions or after session expiration.

---

## 3. Constraints

1. **Pure Python & Zero New Dependencies:** Pure stdlib + existing `re` and `os.path`.
2. **Fail-Open & Fallback Invariant:** If hotspot parsing fails, throws, or times out, the system seamlessly falls back to standard AST skeletonization without error.
3. **Cross-Platform Path Alignment:** Match paths across Windows backslashes (`\`) vs POSIX slashes (`/`), and relative paths vs absolute paths (matching normalized suffixes).

---

## 4. Approaches Considered & Trade-offs

| Approach | Description | Pros | Cons / Trade-offs | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **A: Full-File Bypass on Error** | If a file is in the recent traceback, pass the entire file verbatim without skeletonization. | Trivial to implement (3 lines). | Explodes context tokens if the failing file is 2,000+ lines long. | **Rejected** (wastes context). |
| **B: Dynamic Window Expansion + Caller Tracing** | Trace callers across imports to protect entire call graph. | Catches indirect caller contexts. | High latency, complex AST dependencies, risks exceeding guard budget. | **Rejected** (over-engineered). |
| **C: Minimalist In-Memory Hotspot Hook (Selected)** | Extract file & line from terminal failure; pass `focus_lines` to `smart_skeletonize_code` to protect only the affected function. | Minimal diff (~35 lines), sub-0.2ms latency, optimal token balance, zero extra turns. | Hotspot is lost if process restarts (acceptable in ephemeral CLI). | **Selected Approach.** |

---

## 5. Detailed Technical Design

### Component 1: Traceback Hotspot Extraction (`trimtok/signals.py`)
Add `extract_traceback_hotspots(text: str) -> list[tuple[str, int]]`:
- Scans for standard traceback and compiler diagnostic formats:
  - Python: `File "([^"]+)", line (\d+)`
  - Compilers / Pytest: `(?:^|\b)([A-Za-z0-9_./\\-]+\.[a-zA-Z0-9]+):(\d+)(?::\d+)?`
- Filters out non-source files (`.py`, `.ts`, `.js`, `.rs`, `.go`, `.c`, `.cpp`, `.java`, etc.).
- Returns up to 5 unique `(norm_path, line_no)` tuples per execution.

### Component 2: Session Hotspot Storage (`trimtok/dedup.py`)
In `_seen(session_id: str)`:
- Add a `"hotspots": {}` dictionary mapping `norm_path -> list[int]` (capped at 10 files).
In `compress_tool_result`:
- For `tool_name == "terminal"`:
  - If `exit_code != 0` or `detect_kind(inner_text) in ("log", "traceback")` or `is_important` matches:
  - Extract hotspots and update `_seen(session_id)["hotspots"]`.

### Component 3: Hotspot Query & Slicing (`trimtok/dedup.py`)
In `_handle_read_file_v42`:
- Check if `norm_path` matches any key in `_seen(session_id)["hotspots"]` (using normalized path or suffix match).
- If matched, retrieve `focus_lines = hotspots[key]`.
- Pass `focus_lines=focus_lines` into `smart_skeletonize_code` and `skeletonize_code_full`.

### Component 4: Function Protection in AST Skeletonizer (`trimtok/pruners.py` & `ts_skeleton.py`)
In `smart_skeletonize_code(..., focus_lines=None)`:
- Convert `focus_lines` to `focus_set = set(focus_lines)`.
- During AST function node iteration (`node.lineno` to `node.end_lineno`):
  ```python
  fn_start = getattr(node, "lineno", body_start)
  fn_end = getattr(node, "end_lineno", body_end)
  if focus_set and any(fn_start <= fl <= fn_end for fl in focus_set):
      continue  # Skip folding: keep hotspot function verbatim!
  ```
- Repeat identical guard in `_skeletonize_ts_core` for multi-language AST.

---

## 6. Verification Plan & Test Matrix

1. **Unit Test (`tests/test_causal_hotspot.py`):**
   - Test 1: Python traceback extraction (`File "trimtok/core.py", line 540`).
   - Test 2: Pytest error extraction (`tests/test_foo.py:42: in test_foo`).
   - Test 3: Cold read of multi-function file without hotspot -> all oversized functions folded.
   - Test 4: Cold read of multi-function file WITH hotspot -> hotspot function kept verbatim, other functions folded.
   - Test 5: Path format resilience (forward slash vs backslash, absolute vs relative).
2. **Regression Check:**
   - Execute full test suite: `python -m unittest discover tests`.
   - Execute real corpus benchmark: `python tests/test_real_corpus.py` (ensure 246/246 checks remain green).
