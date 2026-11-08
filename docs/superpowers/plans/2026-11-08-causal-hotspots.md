# Causal Hotspots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically preserve failing functions verbatim during `read_file` calls when preceded by a terminal error/traceback on the same file, eliminating the multi-turn retrieval penalty while maintaining aggressive context compression for all unrelated functions.

**Architecture:** A lightweight in-memory session bridge: `signals.py` extracts failure hotspots `(file_path, line_no)` from terminal outputs via fast regex; `dedup.py` caches them in `_seen(session_id)["hotspots"]`; `_handle_read_file_v42` routes matching `focus_lines` into `smart_skeletonize_code` and `ts_skeleton.py` to selectively bypass function folding on the failing line.

**Tech Stack:** Python 3.10+ (pure stdlib: `re`, `os.path`, `ast`).

**Spec:** `docs/superpowers/specs/2026-11-08-causal-hotspot-design.md`

## Global Constraints

- **Zero External Dependencies:** Pure Python standard library only.
- **Fail-Open Invariant:** Any exception or timeout in hotspot extraction or AST protection must fail-open to default uncompressed text or standard skeletonization without raising.
- **Latency Budget:** Total hotspot extraction and lookup overhead must stay < 0.5ms per tool call to preserve the 200ms guard margin.
- **Cross-Platform Path Alignment:** Match paths across Windows `\` vs POSIX `/`, case insensitivity (`os.path.normcase`), and relative vs absolute path representations via suffix matching.
- **Preserve Existing Invariants:** Must not break any of the 246/246 real corpus regression checks or line prefix integrity (`LINE|`).

## Review Focus

1. **Absolute vs Relative Path Suffix Mismatch:** Terminal outputs `File "trimtok/core.py", line 540` while `read_file` receives `C:\Users\gugmn\Workspace\trimtok\trimtok\core.py`. Path matcher must handle both directions safely.
2. **Compiler Error Formats with Column Numbers:** `path/to/file.rs:42:10: error` or `foo.ts(42,5): error` must cleanly extract line 42 without parsing column numbers as lines.
3. **Multi-Error Truncation / Flooding:** Terminal output emitting 500 lines of tracebacks must be bounded (cap at top 5 distinct files, 10 lines per file) to prevent memory bloat.
4. **Target Function at File Boundaries:** Failing line at line 1 (module docstring/import) or line N (trailing return/raise) must not cause `IndexError` in AST line indexing.
5. **Session Isolation:** Hotspots recorded in `session_A` must never affect `read_file` calls in `session_B` or persist across session clear.

---

### Task 1: Traceback Hotspot Extraction in `trimtok/signals.py`

**Files:**
- Modify: `trimtok/signals.py`
- Test: `tests/test_causal_hotspot.py`

**Interfaces:**
- Produces: `extract_traceback_hotspots(text: str, max_files: int = 5) -> dict[str, list[int]]`
  Returns a mapping of normalized file path string to sorted list of 1-indexed line numbers.

- [ ] **Step 1: Write the failing tests for hotspot extraction**

Add test cases in `tests/test_causal_hotspot.py`:
- Test Python traceback: `File "trimtok/core.py", line 540, in compress_terminal`
- Test Pytest failure line: `tests/test_diff_lane.py:28: in test_diff_lane`
- Test compiler error line: `src/main.rs:42:15: error: cannot borrow`
- Test non-code filter: ignore non-source extensions (`.txt`, `.log`, `.json`)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_causal_hotspot.py`
Expected: FAIL with `ImportError` or `AttributeError: extract_traceback_hotspots`

- [ ] **Step 3: Implement `extract_traceback_hotspots` in `trimtok/signals.py`**

Implement fast compiled regex scanning for Python traceback (`File "...", line N`) and compiler/pytest format (`(?:^|\b)([A-Za-z0-9_./\\-]+\.[a-zA-Z0-9]+):(\d+)`). Normalize path using `os.path.normcase(os.path.normpath(...))`, filter for valid source code extensions, and return `dict[str, list[int]]` bounded by `max_files`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_causal_hotspot.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trimtok/signals.py tests/test_causal_hotspot.py
git commit -m "feat(signals): add extract_traceback_hotspots"
```

---

### Task 2: AST Function Protection with `focus_lines` in `pruners.py` and `ts_skeleton.py`

**Files:**
- Modify: `trimtok/pruners.py:482-585`
- Modify: `trimtok/ts_skeleton.py:190-285`
- Test: `tests/test_causal_hotspot.py`

**Interfaces:**
- Consumes: `focus_lines: set[int] | list[int] | None`
- Modifies: `smart_skeletonize_code(..., focus_lines=None)`
- Modifies: `skeletonize_code_full(..., focus_lines=None)` and `_skeletonize_ts_core(..., focus_lines=None)`

- [ ] **Step 1: Write the failing tests for AST focus protection**

In `tests/test_causal_hotspot.py`:
- Create a multi-function Python module (e.g., `func_a` 25 lines, `func_b` 25 lines).
- Verify without `focus_lines`: both functions are folded into `[tt:body ...]`.
- Verify with `focus_lines=[30]` (inside `func_b`): `func_a` is folded, but `func_b` is preserved 100% verbatim with its original `LINE|` prefixes.
- Repeat test for `ts_skeleton.py` multi-language code.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_causal_hotspot.py`
Expected: FAIL with `TypeError: smart_skeletonize_code() got an unexpected keyword argument 'focus_lines'`

- [ ] **Step 3: Implement `focus_lines` guard in `trimtok/pruners.py` and `trimtok/ts_skeleton.py`**

In `smart_skeletonize_code`:
```python
focus_set = set(focus_lines) if focus_lines else None
# During node loop:
fn_start = getattr(node, "lineno", body_start)
fn_end = getattr(node, "end_lineno", body_end)
if focus_set and any(fn_start <= fl <= fn_end for fl in focus_set):
    continue  # Keep hotspot function verbatim
```
Apply equivalent logic in `_skeletonize_ts_core` in `trimtok/ts_skeleton.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_causal_hotspot.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trimtok/pruners.py trimtok/ts_skeleton.py tests/test_causal_hotspot.py
git commit -m "feat(pruners): support focus_lines bypass in smart_skeletonize_code and ts_skeleton"
```

---

### Task 3: Session Hotspot Wiring in `trimtok/dedup.py`

**Files:**
- Modify: `trimtok/dedup.py:93-111, 1260-1320, 1545-1571`
- Test: `tests/test_causal_hotspot.py`

**Interfaces:**
- Consumes: `extract_traceback_hotspots` from `trimtok.signals`
- Consumes: `focus_lines` in `smart_skeletonize_code` and `skeletonize_code_full`

- [ ] **Step 1: Write the failing test for cross-tool hotspot flow**

In `tests/test_causal_hotspot.py`:
- Call `compress_tool_result("terminal", raw_error_payload, session_id="test_sess")` with a Python traceback pointing to `trimtok/core.py:540`.
- Call `compress_tool_result("read_file", large_file_content, args={"path": "trimtok/core.py"}, session_id="test_sess")`.
- Assert that the function containing line 540 is NOT folded, while other functions in `trimtok/core.py` ARE folded.
- Assert that in a different session `session_id="other_sess"`, the same file has all oversized functions folded.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_causal_hotspot.py`
Expected: FAIL because `dedup.py` does not yet store or route hotspots.

- [ ] **Step 3: Implement session hotspot storage and routing in `trimtok/dedup.py`**

1. In `_seen(session_id)`: initialize `"hotspots": {}`.
2. In `compress_tool_result` (`tool_name == "terminal"`):
   - Check if `envelope.get("exit_code") != 0` or error signals are present.
   - Call `extract_traceback_hotspots(inner_text)`.
   - Update `_seen(session_id)["hotspots"]` (capped at 10 files).
3. In `_handle_read_file_v42`:
   - Match `norm_path` against `seen["hotspots"]` (exact match or path suffix match).
   - If match found: extract `focus_lines = hotspots[k]` and pass to `smart_skeletonize_code` and `skeletonize_code_full`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_causal_hotspot.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trimtok/dedup.py tests/test_causal_hotspot.py
git commit -m "feat(dedup): wire terminal error hotspots to read_file AST skeletonizer"
```

---

### Task 4: Full Suite & Real-Corpus Regression Verification

**Files:**
- Verify: `tests/test_real_corpus.py`
- Verify: `tests/` (all 40 test modules)

- [ ] **Step 1: Run the full unit test suite**

Run: `python -m unittest discover -s tests`
Expected: 94+ tests PASS, 0 failures.

- [ ] **Step 2: Run the real corpus regression suite**

Run: `python tests/test_real_corpus.py`
Expected: 246/246 checks PASS, 0 failures, corpus reduction floor maintained.

- [ ] **Step 3: Commit and record in CHANGELOG**

Update `CHANGELOG.md` with v5.8.1 entry describing Causal Hotspots.
```bash
git add CHANGELOG.md
git commit -m "docs(changelog): document causal hotspots in v5.8.1"
```
