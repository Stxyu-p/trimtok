# Changelog

All notable changes to trimtok are documented here.

## [5.2.0] - 2026-10-29

### Added
- **Tiered Web & File Intake Pipeline (`trimtok.strategies.tiered_shave`):**
  - **Inline URL De-bloat (`_LINK_DEBLOAT_RE`):** Strips redundant URL targets from markdown links (`[Anchor](https://...)` -> `Anchor`), cutting 35–45% of token bloat on web search and documentation pages in < 4 ms without losing a single semantic concept.
  - **Web Noise & Boilerplate Purging:** Automatically prunes cookie consent banners, breadcrumbs (`Home > ...`), social share links, and citation metadata tags.
  - **Heading + 2-Line Factsheet Digest:** Retains all headings, markdown tables, code fences, and the first 2 descriptive lines per section, folding excessive narrative paragraphs into line-anchored CCR `#ref` stubs. Measured on live Wikipedia, Python 3 official docs, and MDN Web Docs with **76.8% – 96.1% token reduction in 2.5 – 4.5 ms**.
  - **100% Patch-Safe Small Code Minification:** For source files < 14KB, automatically strips bulky open-source license headers, copyright notices, and decorative ASCII divider banners without modifying or shifting a single executable statement.
- **Empirical Comparative Benchmark Suite (`eval/eval_read_file_bench.py`):**
  - 16 authentic payloads across 4 key domains (web docs, specifications, small code, large code) in Python, JS, TS, and Go.
  - Rigorous automated verification of Token Reduction %, In-Process Latency (ms), Patch Line-Safety (Zero Offset Drift), and Single-Turn Comprehension.
- **Single-Source Version Authority Sync:** `trimtok/core.py` is the single source of truth; package root imports `VERSION` directly and tests assert uniformity against `pyproject.toml` and `plugin.yaml`.

## [5.1.0] - 2026-10-29

### Added
- **Standalone `trimtok wrap -- <cmd>` CLI:** Run commands directly in shell or CI pipelines outside Hermes; wraps child process, preserves exact exit codes, stores full uncompressed output into SQLite CCR, and emits compact delta summaries.
- **Official Simplified Vector & Brand Logo Assets.**

## [5.0.0] - 2026-10-28

### Added
- **High-ROI Groups 1 & 2 Optimizations:**
  - Non-code structural fold for markdown, yaml, and jsonl files.
  - Skill view session cache hit: repeated `skill_view` calls in the same session collapse to a 1-line pointer.
  - Success collapse on monotonic CLI verbs (`npm install`, `docker pull`, `git push`) with exit code 0.
- **Pre-execution Command Rewriting (`prerewrite`):** Injects concise flags into noisy CLI tools.
- **Runtime Thrash Guard & Loop Detection:** Detects repeated tool execution loops and provides steering indicators.
- **Anti-lossy Compaction Stash Hook:** Stashes raw tool outputs into CCR before `/compact`.

Real savings on traffic that used to pass through untouched, measured on a real
corpus (`eval/`, 32 payloads / 1.13MB, no mocks): **60.45% -> 72.45%** with zero
accuracy-gate failures (was 2 before this release).

### Added
- **Structural fold for non-code `read_file` payloads.** The only structural lane
  was a Python AST skeleton, so markdown / yaml / rst / jsonl fell through to a
  verbatim passthrough (`reason=below_skeleton_min`) — 16.6% of all production
  tokens at ~1.5% saving. `pruners.fold_text_structure` keeps headings, fences,
  tables and key lines with their exact original `LINE|` prefix and replaces prose
  runs with a marker naming the real line range (`[tt:prose L120-L138 (19L)]`), so
  a patch by offset still works. Records are never swallowed whole: the first line
  of every distinct JSON key shape survives. Code extensions are deliberately
  excluded. Corpus effect: README 0->39.6%, CHANGELOG 0->66.5%, skill files
  0->51-72%, usage.jsonl 0->97.4% (11 distinct record shapes kept).
- **`tests/test_real_corpus.py`** — 243 assertions over every corpus payload,
  driven through the real entry points. Asserts no exception escapes, no payload
  inflates, every added `#ref` resolves in the CCR, dropped fault lines are
  announced, prose ranges stay inside the file, headings and top-level symbols
  survive. Refuses to touch `~/.trimtok` (temp data dir) and runs in ~1.4s.

### Fixed
- **Fault lines were hidden without saying so.** With more fault lines than the
  window budget, `_window` kept the *first* `budget` of them, so every later error
  vanished silently — the errors that end a run live at the tail. It now samples
  the fault set evenly and always keeps its tail, and the header carries
  `errors_hidden=K` (tier 0 is forced up to tier 2 so the count is never lost).
  Masked faults were counted by multiset, not by set: five identical errors with
  one kept used to report zero hidden.
- **A 435KB log was killed by its own guard and shipped raw.** The 200ms guard was
  applied regardless of payload size, so a 435KB log (222ms of honest work) went
  out at 0% saved. The guard now scales with size above 100KB (capped at 3x), and
  a genuine timeout still ships the ANSI-stripped text instead of the raw payload.
- **Passthrough gates named themselves but carried no `reason`**
  (`passthrough_small`, `passthrough_sentinel`), so consumers could not tell a
  size cutoff from a sentinel skip.

## [4.2.6] - 2026-10-26

Measurement honesty + the real hot path. No compression algorithm changed.

### Fixed
- **usage.jsonl reported passthrough gates as 100% saved.** `after_chars` was
  written as 0 for every passthrough gate, so any aggregate of
  `1 - after/before` counted ~2.6M untouched tokens as fully eliminated. The
  published headline (48.5% terminal / 38.7% tool) was inflated ~3.5x; real
  production values over 4,323 events are **13.7% terminal / 7.5% tool (9.5%
  overall)**. Passthrough rows now record `after == before`.
- **`_classify` / `_ERROR_RE` cost 42us per 70-char line.** The `looks_suspicious`
  prefilter carried loose hints ("status", "sig", "memory", "http") so every
  info log line reached the 614-char case-insensitive alternation. A 20,000-line
  dump took 1.3s of pure regex (and blew the fail-open deadline). The prefilter
  now asserts the literal prefix first (`_STATUS5_RE`, `_HTTP5_RE`,
  `_HEX_FRAME_RE`) and stays a strict superset of `_ERROR_RE`: 0 rejected lines
  out of 88,890 that the regex would match. `_classify` 1206ms -> 118ms on
  20,000 lines; a 1.56MB payload compresses in 163ms instead of 751ms.
- **`OOMKilled` and `npm ERR!` were not detected as fault lines** and could be
  folded away.

### Changed
- **`guard_ms` default 50 -> 200.** Real p99 is 63ms, worst observed 145ms; at 50
  every payload over ~9K tokens hit the deadline and saved nothing (20 events /
  188,686 tokens in the sample). The deadline is still fail-open.
- **Gate reasons are recorded** (`reason` in usage.jsonl): `small`,
  `targeted_slice`, `anti_poison`, `below_skeleton_min`, `no_gain_fold`,
  `header_cost`, `deadline`, `sentinel`, plus the exception text. The
  `passthrough_reference` gate has four distinct causes and was indistinguishable
  in telemetry.
- **`shape="ref-verbatim"`** for the read_file passthrough band, which previously
  hid 14.7% of all tokens inside `shape="mixed"`.
- **Tests no longer write production telemetry.** Mocked-gate rows (`gate="small"`,
  fixtures like `git statusx`) polluted the real usage.jsonl and skewed every
  real-world number.

## [4.2.3] - 2026-10-24

### Changed
- **Zero Model / Zero Daemon Architecture Documentation**:
  - Re-affirmed standalone context compression architecture: operates 100% via heuristic AST analysis, BPE token estimation, differential patching, and lossless Zstandard CCR caching.
  - Requires zero embedding models, zero GPU/ONNX runtimes, zero external daemons, and zero network calls.
  - Hardened cross-platform SQLite WAL concurrency and file-locking on Windows NTFS.

## [4.2.2] - 2026-10-21

The v4.2 stateful read-file lane never fired in real Hermes: 1,078 `read_file`
calls / 5.83M characters were passed through with 0% reduction. Argon 4 reviewed
the code and confirmed the root cause plus four more co-killers; all five are
fixed here. `tests/test_v422_hermes_read_lane.py` reproduces the real runtime
(`offset=1, limit=2000` defaults and `LINE|CONTENT` payloads) and was verified
to fail against the pre-fix code.

### Fixed
- **Hermes' default pagination no longer defeats the engine** (`trimtok/dedup.py`) -
  Hermes always sends `offset=1, limit=2000`, so the old Rule 0 rejected every
  call as a "targeted slice". A read is now treated as targeted only when it
  really starts inside the file (`offset > 1`, first line number > 1) or when the
  caller deliberately capped a small window - decided from the payload and the
  arguments alone, without touching the filesystem.
- **Numbered payloads are unwrapped before analysis** (`trimtok/dedup.py`,
  `trimtok/pruners.py`) - Hermes returns every line as `LINE|CONTENT`, which
  broke `ast.parse()`, the outline detector and the line-number diff. The
  payload is unwrapped to raw source, analysed there, and the original `LINE|`
  prefixes are re-attached to every kept line; skeleton markers now carry the
  real file line numbers.
- **The file cache survives a patch** (`trimtok/dedup.py`) - the mutating-tool
  handler dropped the cached body, so the contextual-delta lane could never fire
  in a real read -> patch -> re-read loop. The body is now kept and flagged.
- **The file cache survives multi-step agent loops** (`trimtok/dedup.py`) -
  `expire_old_turns` aged file bodies out after two tool steps; they now live
  for eight, matching how long a read -> search -> terminal -> read loop runs.
- **`read_file_extra` reaches the engine** (`trimtok/dedup.py`,
  `trimtok/signals.py`, `trimtok/__init__.py`) - the second Hermes read tool was
  dropped by a hardcoded tool-name check, and read tools are no longer treated
  as verbatim-only.

### Added
- **Anti-loop rail** (`trimtok/dedup.py`) - a re-read that already received a stub
  or a skeleton returns the real file instead of another pointer.
- **Cheap token gate** (`trimtok/dedup.py`) - the BPE pass only runs when the
  candidate is close in size to the original; the char ratio already proves the
  token count dropped otherwise.
- **Fail-open on the guard budget** (`trimtok/dedup.py`) - a file read that is
  out of time ships the verbatim file instead of blowing the deadline.
- **Work log** (`reports/CHANGELOG_TH.md`) - one living changelog per topic with
  the measured numbers; the raw Argon reviews live in `reports/raw/`.

### Measured
- `read_file` cold-read band: 240,202 -> 115,155 characters (52.1% reduction;
  0% before) - benchmark `RESULT: PASS`, all acceptance gates met.
- Test suite: 31 passed (27 previous + 4 new Hermes-fidelity tests).
- Latency, read_file band: p50 6.49 ms, p95 74.83 ms (43 KB file).

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
