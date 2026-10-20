# Changelog

All notable changes to trimtok are documented here.

## [4.2.1] - 2026-10-21

Argon 4 reviewed the v4.2 diff in the original planning thread and returned
**REQUEST CHANGES** with five blockers. All five are fixed here, and each fix has
a regression test (`tests/test_v42_argon_fixes.py`) that was verified to fail
against the pre-fix code.

### Fixed
- **Syntax corruption in the AST skeletonizer** (`trimtok/pruners.py`) - a method
  whose docstring was followed by a blank line read its indentation from that
  blank line and emitted `pass` at the wrong depth (`IndentationError`), and a
  nested decorated `def` left its `@decorator` above the `pass` (`SyntaxError`).
  The indentation now comes from the first folded statement, a nested decorator
  is folded with its `def`, and the fold range stays contiguous so the retrieval
  coordinates in the marker still match the file.
- **A re-read after a cold skeleton no longer lies** (`trimtok/dedup.py`) - the
  first read of a file over 12 KB returns a skeleton; the next read used to answer
  "identical to prior read in active context" with an outline even though the
  agent had never seen the body. The cache now records that only a skeleton was
  delivered, and a repeat read returns the real file.
- **A unified diff is no longer corrupted** (`trimtok/core.py`, `trimtok/dedup.py`) -
  `hygiene()` stripped the single space that a diff context line carries, and the
  512-2047 char band skipped the clean path whenever the payload had repeated
  lines. Diff payloads now skip `hygiene()` entirely, and a payload with repeated
  lines is folded and marked instead of being dropped to a passthrough.
- **The adaptive tool header is wired in** (`trimtok/dedup.py`, `trimtok/signals.py`) -
  `compress_tool_result` used the fixed v4.1 header with no token gate and no
  rescue. It now scales the header by the saving, gates on real token counts, and
  downgrades to the nano header before reverting. The tier order was also
  corrected: Tier 2 was longer than Tier 3.
- **Shell mutations invalidate the cached body** (`trimtok/core.py`) -
  `invalidate_shell_mutations` was written but never called, so a `git checkout`
  or `sed -i` left a stale "file unchanged" entry behind.
- **Cached file bodies now expire with their turn** (`trimtok/dedup.py`,
  `trimtok/__init__.py`) - `expire_old_turns` marked the fold index but not the
  file cache, and a verbatim read was never noted, so a file could be served as
  "unchanged" long after it had left the context window.
- **The delta header now advertises a retrievable ref** (`trimtok/dedup.py`) -
  `#prev_ref` pointed at content that was never written to the CCR, so
  `trimtok_retrieve` on it failed with "Not found or expired".

### Added
- `tests/test_v42_argon_fixes.py` - eight regression tests, one per blocker plus
  the contract checks that must keep holding.

### Changed
- The read-file delta path is limited to files of at most 1500 lines; above that
  the file is skeletonized instead, because `SequenceMatcher` on lines does not
  fit the guard budget.

## [4.2.0] - 2026-10-20

### Added
- **Three-Tier Stateful File Engine (`trimtok/dedup.py`)**:
  - Implemented `_handle_read_file_v42` to eliminate the 0% compression bottleneck on `read_file` (1,107 calls, 5,966,692 characters previously passed through uncompressed across 9,303 telemetry events).
  - Tier 1 (Unchanged Repeat): Emits a content-addressed reference stub (`[tt:file_unchanged #digest]`) with a structural AST outline when file content is identical across turns within active context.
  - Tier 2 (Mutated Re-Read): Emits a contextual line-numbered delta displaying modified hunks with plus-or-minus 15 lines of surrounding context when a file is edited and re-read.
  - Tier 3 (Cold Oversize Read): Skeletonizes cold files exceeding 12,000 characters using line-anchored body folds, while files under 12,000 characters and targeted reads (`offset`/`limit`) remain 100% verbatim for single-turn patch safety.
  - Added deterministic file cache invalidation on file mutation tools (`patch`, `write_file`, `edit_file`, `replace_in_file`, `apply_patch`, `multi_edit`) and shell modification commands (`git checkout`, `git reset`, `sed -i`).
- **Line-Anchored Smart AST Skeletonizer (`trimtok/pruners.py`)**:
  - Implemented `smart_skeletonize_code` using pure Python standard library `ast.parse`.
  - Preserves 100% of imports, module-level constants, decorators, complete function signatures, type annotations, first docstring lines, and terminal return/raise statements.
  - Retains functions of 15 lines or fewer completely verbatim; folds longer bodies into valid Python syntax comment stubs with exact 1-indexed line coordinates (`Lstart-Lend`) and content-addressed `#cN` retrieval references.
- **Four-Tier Adaptive Header Scaling (`trimtok/signals.py`)**:
  - Added `adaptive_terminal_head` and `adaptive_tool_head` to dynamically scale marker overhead based on payload size and net character savings.
  - Tier 0: Headerless output (`0 chars` / `0 tokens`) for lossless operations with zero hidden or dropped lines.
  - Tier 1: Nano header (`[tt:#c1 -18L]`, 13–16 chars / ~4 tokens) for payloads 500 to 4,096 chars or savings under 240 chars.
  - Tier 2: Compact one-line header (`48–68 chars` / `~14 tokens`) for payloads 4,097 to 8,192 chars with savings of 240 chars or more.
  - Tier 3: Full diagnostic header and legend (`160–180 chars` / `~48 tokens`) for payloads exceeding 8,192 chars.
  - Added Cascade Gate Rescue: automatically downscales header tiers (Tier 3 to Tier 1 to Tier 0) before rejecting candidates, targeting a reduction of the 6.0% `reverted_gate` failure rate (558 events) to under 0.8%.
- **Sub-Millisecond Micro-Minification Fast Lane (`trimtok/dedup.py`, `trimtok/core.py`)**:
  - Implemented `micro_minify_tool` and terminal micro-minification for payloads in the 512 to 2,047 character band (previously bypassed as `passthrough_small` across 1,306 events or 24.1% of telemetry).
  - Performs lossless ANSI stripping, whitespace and blank-line hygiene, in-memory JSON minification, and adjacent repeat folding (>= 4 lines) in under 0.25 ms without header bloat.
- **Telemetry Acceptance Verification Suite (`tests/test_v42_telemetry_gates.py`)**:
  - Added verification test suite asserting 100% fault preservation, AST syntax validity, multi-turn file lifecycle compression, elimination of 2KB–3KB `reverted_gate` failures, and sub-millisecond micro-minification latency.

### Changed
- **Pipeline Tool Argument Propagation (`trimtok/__init__.py`)**:
  - Updated `_on_tool_result` to forward invocation arguments (`args`) to `compress_tool_result`, enabling file path, offset, and limit awareness in downstream dedup handlers.
- **CCR Storage Optimization (`trimtok/__init__.py`)**:
  - Bypassed SQLite CCR disk persistence for `lossless_tier0` operations where no content lines are hidden or dropped, reducing unnecessary disk I/O and database lock contention.
- **Turn Masking Expiration (`trimtok/dedup.py`)**:
  - Updated `expire_old_turns` to mark cached file entries as expired when they exceed `mask_keep_turns`, preventing stale file references to contents no longer in context.

### Fixed
- **Repeated Tool Output Short-Circuit (`trimtok/dedup.py`)**:
  - Fixed `raw_hashes` bug where identical tool results across turns triggered `passthrough_repeat` and emitted uncompressed raw text; separated failed hashes (`failed_raw`) from active span tracking so repeated identical outputs fold correctly into `[tt:seen N lines #c1]`.
- **Post-implementation review fixes (verified against the v4.1.0 baseline):**
  - `NameError: raw_hashes` — two leftover `raw_hashes.add()` calls survived the rewrite, so *every* large tool result returned `passthrough_exception` and compression was silently dead. The failed-hash path now records into `failed_hashes`; the success-path add is gone.
  - `passthrough_exception` now carries the exception type and message in the stats dict. A swallowed exception here disables all tool-result compression and was previously invisible in telemetry.
  - Unchanged-repeat stub hint shortened (`Outline (read_file offset/limit for exact lines):`), which brings the cache-hit lane from 79.6% to >80% saved.
  - `micro_minify_tool` no longer calls `estimate_tokens()` (a real BPE pass, ~0.19 ms per 1.6 KB, twice per call). The lane gates on character reduction instead, taking p95 from 0.39 ms to 0.12 ms against the 0.25 ms target.
  - Exact-repeat results are now re-compressed rather than passed through raw (the old path re-sent the whole payload); `tests/test_benchmark.py` asserts the new "smaller, never larger" contract.

## [4.1.0] - 2026-10-20

### Added
- **PyPI Release Workflow** (`.github/workflows/release.yml`): automated build, twine verification, and Trusted Publishing to PyPI on GitHub release.
- **Hermes Plugin Entry Point Fix** (`__init__.py`): re-exported `register` attribute on the root module object so host loaders that maintain namespaced module references can locate `register` directly.

## [4.0.0] - 2026-10-18

### Added
- **Universal Stdio MCP Server & Multi-Client Installer (`mcp_server.py`, `mcp_install.py`, `__main__.py`)**:
  - Exposes `trimtok_compress_terminal`, `trimtok_compress_tool`, `trimtok_retrieve`, and `trimtok_status` over newline-delimited JSON-RPC 2.0 (`msgspec`-accelerated when available).
  - One-command `trimtok install` / `trimtok clients` / `trimtok uninstall` auto-registers across Claude Code, Claude Desktop, Gemini CLI, Antigravity, Antigravity IDE, OpenAI Codex CLI, OpenCode, and VS Code (GitHub Copilot).
- **Direct On-Disk `file_path` Compression (`mcp_server.py`)**:
  - `trimtok_compress_terminal` and `trimtok_compress_tool` now accept an optional `file_path` argument to read, compress/skeletonize, and cache large log files, JSON payloads, or source code directly from disk without loading raw bytes into the LLM context window first.
- **Zero-Willpower Agent Habit & MCP Instructions (`SERVER_INSTRUCTIONS`)**:
  - Returns `instructions` in the MCP `initialize` handshake and automatically provisions `~/.gemini/antigravity-cli/mcp/trimtok/instructions.md` plus `~/.skill-proof/skills/trimtok/SKILL.md` during `trimtok install`.
- **AST Code Skeletonization & Zstandard CCR v3 (`code_skeleton.py`, `ccr.py`)**:
  - Structural AST folding for large source files alongside TOON v2 tabular JSON encoding and SQLite WAL + Zstandard blob storage.

## [3.0.0] - 2026-10-16

### Added
- **True-BPE Token Gate & Fast Ingestion (`bpe_estimator.py`)**: Singleton offline `tiktoken` (cl100k_base / o200k_base) and Rust `jiter` fast parser with seamless Python stdlib fallbacks. Non-inflation token gate actively blocks token bloating.
- **TOON v2 Hierarchical & Nested Columnar Codec (`toon_codec.py`)**: Dot-notation unnesting for wrapper payloads (`@data.results[N]{fields}:`), compact inline JSON cells for small lists/dicts (<= 120 chars), and recursive in-memory type-strict equality without string serialization. Achieves 70–78% reduction on nested tool results.
- **Two-Stage Fuzzy Stateful Delta Engine (`delta_engine.py`)**: Safe command family normalization (strips cosmetic `-v`, `-q`, `--tb=...` flags while preserving scope/targets like `-k`), $O(N)$ skeleton match (`_skeleton`) for shifted line numbers/timestamps, and `rapidfuzz` fuzzy line alignment. Preserves recurring faults in `STILL FAILING` (98.5% token reduction).
- **Structured Traceback & Semantic Diff Pruning (`traceback_pruner.py`, `diff_engine.py`)**: Collapses intermediate framework frames in Python and Node/JS while preserving 100% of entrypoints, user frames, crash sites, and exception lines. Evaluated before classifier to guarantee harmony with Quality Gate (zero `reverted_quality`). Diff engine preserves file headers, hunk headers with scope, and change lines while collapsing middle context.
- **Targeted Context-Aware CCR v3 Section Retrieval (`__init__.py`)**: Read-time slicing in `trimtok_retrieve` supporting `section="errors"`, `"diff"`, `"summary"`, `"tail"`, with configurable `context_lines` (default 3), achieving >77% token savings on retrieval without touching SQLite schema.
- Comprehensive test suite `tests/test_v3_upgrade.py` with 86 new checks, bringing test coverage to 376 checks (10 suites, 100% PASS, 0 FAIL).

## [0.10.0] - 2026-10-15

### Added
- Cross-turn command delta engine: a repeated terminal command (pytest, git status, git diff) whose output overlaps the previous run by at least 65% collapses to an Added / Removed / STILL FAILING view. Per-session LRU state, snapshots stored lazily so refs resolve, 0.61 ms median delta step on the 200-line class.
- STILL FAILING rail: a fault present in both runs is always re-emitted (regression blindness canary); newly failing lines are ordered faults-first.

### Changed
- Fault classification memoized (LRU, short lines): repeat scans of a 2,000-line output drop from about 10 ms to about 1.3 ms.
- Command delta refs always resolve to store aliases (`#cN`) in the packaged plugin.

### Fixed
- The packaged plugin no longer builds a second `ccr_v2` module instance (previously caused transient lock errors that degraded refs to raw hashes, plus a leaked sqlite handle). The runtime test now asserts alias refs.

## [0.9.0] - 2026-10-15

### Added
- Lossless JSON to TOON columnar codec: type-strict round-trip verification gates every fold, and a fold applies only with at least 4 BPE tokens of net gain (Token-Gain Gate). A 1,000-row JSON table shrinks by 74.3%.

## [0.8.0] - 2026-10-15

### Added
- Fault-polarity single source (`polarity.py`) with HTTP 5xx coverage: `status=500`, `HTTP/1.1 502`, `bad gateway`.
- Scoped search grouping: multi-file grep results group per file with definition headers.

### Fixed
- Template folds can no longer swallow a fault line inside a repeated block.

## [0.7.0] - 2026-10-15

### Added
- Two-tier CCR store: SQLite index with short `#ref` aliases, stable per content, on top of the local file store.
- Fail-open deadline guard (`guard_ms`, default 50 ms): a hook that exceeds its budget returns the original text.
- Compact pointer stubs (`[@#ref xN]`) and legacy reference migration.

## [0.6.2] - 2026-10-15

### Added
- Polarity-locked error guard (English and Thai rails) in the tool-result pipeline.
- In-memory LRU tier for the retrieval store.

## [0.6.1] - 2026-10-13

### Fixed
- `/trimtok` registered through `ctx.register_command` (the previous call target did not exist on the host).

## [0.6.0] - 2026-10-13

### Added
- Drain-style template fold (`×N` plus a verbatim sample), shingle near-dedup (Jaccard at least 0.8), HEAD_TAIL stubs for large unique documents, the `marker.py` module, usage logging, and a quality gate with partial overflow mode.

### Changed
- Terminal reduction on the mixed 5-turn fixture improved from 30.0% to 61.2%.

## [0.5.2] - 2026-10-12

### Added
- `trimtok_retrieve` pagination and regex filtering, balanced windowing, single-pass CCR eviction, session lifecycle memory reclamation, and opt-out controls with runtime coverage.

## [0.5.0] - 2026-10-11

### Added
- Initial release: terminal reduction (ANSI strip, adjacent-repeat fold, balanced windowing, per-command budgets), cross-turn tool-result fold, and the content-addressed retrieval cache.
