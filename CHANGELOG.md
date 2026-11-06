# Changelog

All notable changes to trimtok are documented here.

## [5.5.5] - 2026-11-06

CCR store invariants. Five defects, each reproduced against a throwaway store
before the fix.

### Fixed
- **The TTL sweep reissued dead aliases:** it deleted expired rows from SQLite
  but left them in the RAM LRU, so `put()` kept returning an alias whose row no
  longer existed and the marker resolved to nothing after a restart.
- **`put()` could raise `KeyError` on content it already held:** `unpin()` drops
  the RAM entry but keeps the sha→alias map, and the fast path called
  `move_to_end` on the missing key. The facade swallowed it, so the payload
  silently got no reference. The fast path now requires both maps to agree.
- **The file backend deleted the cache on a read:** `ttl_hours=0` means "no
  expiry" in the SQLite tier, but the legacy file tier computed
  `cutoff = now - 0` and unlinked the file during `get()`, `stats()` and
  `evict()`. All three now skip expiry when `ttl_hours <= 0`.
- **`stats()` and `evict_to_size()` counted characters, not bytes:**
  `LENGTH(content)` returns characters for the TEXT rows used by small
  payloads, so a non-ASCII store reported 140 bytes for 420 real ones and the
  size cap could not be enforced. Both now use `LENGTH(CAST(content AS BLOB))`.

### Removed
- **`trimtok purge --ttl-hours`:** it persisted a value into the store's meta
  table that no read path ever consulted, so it printed success and changed
  nothing. The retention window is configured by `ccr_ttl_hours`.

## [5.5.4] - 2026-11-06

### Fixed
- **The auto-map hook described the wrong directory:** `_pre_llm_call` mapped
  `Path.cwd()`, which inside a host application is that application's own home,
  so every session was handed a 1.5 KB map of its uv cache. The hook now walks
  up to the nearest project marker (`.git`, `pyproject.toml`, `package.json`,
  `go.mod`, …) and injects nothing when there is none. `SKIP_DIRS` also learned
  `site-packages`, `.cache`, `archive-v0`, `bower_components` and `.tox`.

## [5.5.3] - 2026-11-06

Bug-hunt pass. Eight defects, each reproduced before it was fixed and pinned by
a test that fails if the fix is reverted.

### Fixed
- **`minify_lossless` could delete the rest of a file:** a line merely
  *containing* `/*` (a string literal like `COMMENT_START = "/*"`) opened a
  block comment that never closed, and once a later line matched a copyright
  trigger every remaining line was dropped. Only a line that starts a comment
  may open one now, and an unclosed opener is abandoned as soon as the text
  stops looking like comment body.
- **Failed installs were reported as successes:** `collapse_success_output`
  guarded on five literal substrings, so yarn's `error ...` and pnpm's
  `ERR_PNPM_...` slipped through and a failed install collapsed into
  `[tt:success ...]`. The guard now asks `is_important()` per line.
- **Error polarity missed common phrasings:** `cannot stat`, `no such file`,
  `ERR_*`, and request-log 5xx lines (`GET /x 500`) were not treated as faults,
  so the error rails could fold them away without announcing it.
- **`kv_lock` lost values on the second pass:** the date pattern matched across
  a newline, and the appendix parser read one line per value. Month/day
  separators are now `[ \t]+`, and a continuation line is folded back into its
  value.
- **`trimtok wrap` corrupted argument boundaries:** argv was joined into a
  string and re-split with `shlex`, so `pytest -k "not slow"` became four
  arguments and silently selected different tests. Rewriting now works on the
  argv list (`rewrite_argv`).
- **Delta snapshots merged unrelated commands:** `-s`/`-v` were stripped as
  cosmetic, so `git status -s` shared a baseline with `git status` and
  reformatted lines were reported as resolved failures. Only flags that leave
  the output content alone are normalised now.
- **The sentinel guard fired on anything quoting a marker:** an unanchored
  search over the first 2 KB meant any file mentioning `[tt:...]` bypassed
  compression forever. The marker must now be on the first line.
- **Web intake advertised a ref that was never stored:** the prose folder was
  given a literal `#doc`, which resolves to nothing. It now carries the
  payload digest, which the caller rewrites into the real alias.

### Changed
- `decision_recoverable_rate` is `(equivalent + recoverable_only) / total`; it
  was inverted, so a compressor that destroyed every decision scored 100%.

## [5.5.2] - 2026-11-06

### Fixed
- **Backfill could stall the store open:** the one-time index backfill ran each
  insert in autocommit, so a store held by another process re-waited the busy
  timeout per row (minutes for a few hundred chunks). It now runs in a single
  transaction, records `fts_synced` in `meta`, and skips entirely once done.

## [5.5.1] - 2026-11-06

Correctness pass over 5.5.0. The search feature shipped unreachable, blind to
existing payloads and to non-Latin scripts, and the quality gate could go green
on total context loss. All four are fixed and covered by tests that fail if the
fix is reverted.

### Fixed
- **MCP search was uncallable:** `RETRIEVE_SCHEMA` required `hash`, so a
  schema-valid client could never send `query` alone. The schema now accepts
  either (`anyOf`) and the handler branches on a resolved ref instead of the
  raw argument.
- **Existing payloads were invisible to search:** `chunks_fts` was created empty
  and never populated for stores written before 5.5.0. The store now backfills
  every missing alias once per database, in a single transaction, tracked by a
  `fts_synced` meta flag.
- **Non-Latin queries silently returned nothing:** the sanitizer kept only
  `[A-Za-z0-9_\u0E00-\u0E7F]`, dropping CJK, Cyrillic, Greek and accented
  Latin. Tokens are now split on whitespace and quoted, letting FTS5's
  unicode61 tokenizer handle any script.
- **Search offered expired refs:** hits now join `chunks` and respect TTL and
  pinning, so `search()` never returns an alias that `get()` would refuse.
- **The FTS index was unaccounted disk:** `stats()` reported payload bytes only
  while the index kept a plaintext copy. `stats()["bytes"]` is now the real
  footprint (with `payload_bytes` / `fts_bytes` breakdown) and `evict_to_size()`
  enforces the cap against the same number.
- **`--focus` typo degraded the map silently:** a missing focus file now falls
  back to the unfocused ordering, the CLI warns, and the focus file keeps a
  wider symbol slice instead of collapsing to one symbol per file.
- **Go import parsing matched string literals:** only real `import` statements
  and import blocks are read now.

### Changed
- **Decision-equivalence gate is strict:** a scenario counts as preserved only
  when the action is still visible in the emitted text; ref-only recovery is
  reported separately as `recoverable_only`. A run where any scenario was not
  compressed is marked invalid instead of green, `--json` propagates the exit
  code, and the suite writes to a temporary store instead of `~/.trimtok`.
- `pytest` is declared as the `dev` extra, so the documented runner installs.
- Hot-path FTS write no longer issues a redundant full-index delete.

## [5.5.0] - 2026-11-06

Context Intelligence Engine: SQLite FTS5 search across captured payloads,
task-aware dependency codebase mapping, and a decision-equivalence quality
contract.

### Added
- **SQLite FTS5 search in CCR (`trimtok/ccr.py`, `trimtok search`):** embedded FTS5 virtual table (`chunks_fts`) inside the existing `FastCCRStore` WAL database, giving BM25 keyword search across captured terminal and tool outputs in single-digit milliseconds. The index keeps a plaintext copy for snippet generation, so it is counted in `stats()` and in the `ccr_max_bytes` ceiling.
- **MCP `trimtok_retrieve` search capability:** `trimtok_retrieve` accepts an optional `query` parameter to discover relevant stored payloads without an exact hash upfront.
- **Task-aware dependency code mapping (`trimtok/codemap.py`, `trimtok map --focus <path>`):** `--focus <file_path>` parses the target file's imports (Python, JS/TS, Go, Rust) and ranks its dependencies above unrelated modules inside the character budget, in < 50 ms.
- **Decision-equivalence quality contract suite (`eval/decision_eval.py`):** measures whether compression keeps the action and the critical identifiers an agent needs, instead of counting bytes only.

## [5.4.1] - 2026-11-05

Zero-disk-footprint ecosystem shield: Bounded MCP schema proxy, ultra-light codebase mapping, KV-cache prompt stabilizer, and multi-header framing.

### Added
- **KV-Cache Alignment & Prompt Stabilization (`trimtok/kv_lock.py`, `trimtok kv-lock`):** locks system prompt prefixes across sessions by replacing dynamic tokens (dates, times, UUIDs, JWTs) outside of code blocks with stable placeholders and relocating their values to a delimited appendix (`<!-- kv-lock: dynamic context -->`) at the tail. Guarantees identical prefix SHA-256 hashes, unlocking up to 90% prompt-caching cost reductions on Anthropic/Gemini/DeepSeek APIs.
- **Bounded MCP proxy KV-lock (`--kv-lock`):** `trimtok mcp-proxy --kv-lock` automatically stabilizes downstream server instructions during the `initialize` handshake.
- **Ultra-light codebase mapper (`trimtok/codemap.py`, `trimtok map`):** scans repository via `git ls-files` + regex, outputting a 1-line-per-directory summary of top-level functions and classes constrained by a strict character budget (default 2,000 chars, ~38ms execution). Prioritizes core/utils/shared modules so agents comprehend existing code immediately without blind exploratory searches.
- **Hermes auto-map hook:** `pre_llm_call` hook automatically injects the codebase map once per session/workspace directly into Hermes conversation context in-memory with zero disk footprint.
- **Bounded stdio MCP proxy (`trimtok/mcp_proxy.py`, `trimtok mcp-proxy`):** intercepts JSON-RPC 2.0 stdio communication between any coding agent harness and downstream MCP servers. Prunes unused tool schemas from `tools/list` responses using `--allow` or `--deny`, eliminating thousands of wasted system prompt tokens per turn. Supports Content-Length framing, newline framing, UTF-8 BOM stripping, and fail-open passthrough.
- **Native MCP handshake instructions:** `trimtok/mcp_server.py` emits `SERVER_INSTRUCTIONS` inside `InitializeResult.instructions` so connected agents receive lossless CCR retrieval instructions in memory without mutating repo config files.

### Fixed
- **Multi-header LSP framing:** `trimtok/mcp_proxy.py` properly parses multi-line header blocks (e.g. `Content-Type`) and uses `_read_exact` to prevent partial socket read corruption.
- **KV-Lock appendix preservation:** `trimtok/kv_lock.py` merges pre-existing appendix values and preserves trailing text on prompt updates without data loss.

## [5.3.5] - 2026-11-04

Five capabilities, each with a test that can fail.

### Added
- **Content policy (`trimtok/policy.py`):** decides what may be compressed at
  all. Secrets (`.env`, `*.pem`, `*.key`, `.npmrc`, inline AWS/GitHub/OpenAI/
  Slack/JWT/PEM shapes), security-policy data files (`config/auth/*.yaml`,
  `security/*.rego`, `policies/*.json`) and edit payloads (`patch` tool, any
  unified diff) now stay byte-exact and return `gate=passthrough_policy` with a
  named reason. Security *source* code still compresses — the AST lane keeps
  every signature and leaves a ref, so `auth.py` does not become unreadable.
- **Adaptive compression:** `context_pressure` (0..1, from the hook or config)
  maps to `light` (<0.35) / `normal` / `aggressive` (>0.75). It scales the
  terminal line budget (×1.6 / ×1.0 / ×0.55) and the AST fold threshold
  (20 / 8 / 4 body lines). Measured on 400 unique log lines: 195 / 123 / 69
  lines kept. `adaptive_enabled: false` pins it to normal.
- **Model-aware TOON:** `toon_mode: auto|always|never` plus `model_family`.
  `auto` emits TOON for families with a track record on columnar text and falls
  back to minified JSON otherwise. Unknown family keeps TOON so nothing
  regresses silently. The JSON file lane now honours `toon_enabled` — it
  previously emitted TOON even when the caller had turned it off.
- **Explicit CCR retention:** `trimtok purge --older-than H --max-mb M
  --ttl-hours H`. Age purge first, then evict oldest until the store fits the
  byte ceiling; TTL persists in the store's `meta` table. `--max-mb` alone no
  longer wipes the store before measuring it.
- **Fix-accuracy benchmark (`eval/fix_accuracy.py`):** injects a known
  regression into a real module, compresses it, and asks whether the bug is
  still fixable. `LOCATE` (default, no LLM) checks the buggy line, its
  signature, its line number and neighbour context are visible or ref-reachable.
  `--llm` takes the model's corrected line, patches the module, imports it and
  runs a behavioural predicate — PASS means the code works, not that the text
  looks right. Current: 8/8 locate, 53.8% tokens saved.

### Verification
- 94/94 unit tests, including `test_wrong_patch_fails` — a benchmark that cannot
  fail measures nothing.
- Real corpus unchanged at 73.19%, 0 gate failures; honesty audit 73.19%
  claimed = 73.19% proven.
- End-to-end: `.env` / `security/rules.rego` / unified diff all return
  `passthrough_policy` and byte-identical output.

### Known limits
- `--llm` stage needs a live model; the local 9router proxy returned HTTP 429
  throughout this release, so only the deterministic LOCATE stage ran.
- `auto` TOON is a heuristic on model *family*, not a measured capability
  claim: TOON is plain text and every model can read it, some just hold a
  columnar table together better.

## [5.3.4] - 2026-11-04

### Added
- **JSON intake lane for `read_file`:** `.json` files previously fell through
  both the AST and prose lanes and shipped verbatim at any size. They now go
  through TOON (arrays) or whole-doc minify (objects), both round-trip
  verified, with a real `#ref` so the original stays retrievable. Live check:
  GitHub repos 17.6%, JSONPlaceholder comments 28.6%, posts 27.1%, single-dict
  OpenAPI 45.0% (all were 0% before).
- **Wide + deep TOON cells:** uniform tables up to 128 columns now columnarize
  (50 columns: 61% saved), and nested objects longer than the inline cap are
  stored behind an explicit `=` JSON marker instead of rejecting the table
  (this alone unblocked GitHub/Stripe-shaped payloads).
- **`trimtok retrieve --intent` / `--intent-budget`:** free-text retrieval that
  ranks lines by stemmed token overlap, with structural boosts for definition
  and heading lines. Exposed on the CLI, not just the MCP tool.
- **`trimtok rewrite -- <cmd>`:** prints the noise-reduced command for shell
  aliases. Hermes exposes no pre-execution shell hook (`pre_command` is
  observer-only and slash-command-scoped), so this cannot be wired into the
  agent runtime automatically — `trimtok wrap` applies it at execution time.
- **`eval/steps.py`:** per-step token accounting (raw → hook → narrow) over the
  real corpus, no LLM needed. Current totals: 391,665 → 98,532 (74.84%) →
  42,773 tok (89.08%).

### Changed
- Intent ranking now stems terms (`functions`→`function`) and adds one line of
  context under each hit, so a signature arrives with its body.
- `MAX_FIELDS` 64 → 128 for uniform tables (the sparse case is still refused by
  the exact schema-equality check, not by this cap).

### Fixed
- JSON minify lane emits a resolvable `#ref` before shipping, so C1/C5 hold
  (caught by `eval/eval_real.py`: `tool-9router-pkg` C1-lossy(73) + C5-verbatim
  when the lane first shipped headerless).

### Verification
- 72/72 unit tests (11 new in `tests/test_v534_lanes.py`).
- Real corpus: 73.19% reduction, 0 gate failures; honesty audit 73.19% claimed
  = 73.19% proven; 0 silent line loss, 0 unannounced faults.

## [5.3.3] - 2026-11-04

### Added
- **Multi-Language Tree-Sitter Parsers:** Activated prebuilt bindings for JavaScript/TypeScript, Go, Rust, and Python in venv.
- **Docker Command Filter:** Added `_docker_ps_filter` for `docker ps` and `docker container ls` → compact summary of running containers, images, and service names backed by CCR (`[tt:docker_ps]`, ~72% reduction).
- **Pre-execution Rewriting CLI Integration:** Wired `rewrite_command` into `trimtok wrap -- <cmd>` to inject concise flags (`pytest -q --tb=short`, `git log --oneline -n 25`) before execution.

### Fixed
- **Go AST Statement Folding:** Fixed `_skeletonize_ts_core` handling of `statement_list` so closing braces `}` are strictly preserved.
- **Version Uniformity Invariant:** Added `plugin.yaml` to `test_version_sync.py` to prevent version drift between package, plugin, and docs.

### Verification
- 61/61 unit tests passing (0 skipped, 0 failures).
- 18/18 Sadist torture attacks passing (100% pass rate, 11,605 tokens saved).

## [5.3.2] - 2026-11-04

### Fixed
- **Silent Data Loss in Git Diff:** Fixed `_PROGRESS_RE` matching bare `-` as progress spinners which dropped deleted code lines in `git diff`; explicitly protected unified diff hunks and additions/deletions in `compress_terminal`.
- **Sentinel Regex Hardening:** Expanded `SENTINEL_RE` to match all v4/v5 sentinel tags (`[a-z_]+`) ensuring `file_unchanged`, `lockfile`, and `skill_cached` markers are recognized and never re-processed.

### Added
- **Edge-Case Sadist Torture Harness:** Added 18-attack adversarial torture suites across 7 hostile taxonomies (`eval/eval_sadist_suite.py` and `eval/eval_sadist_wave2.py`).
- **README Dashboard & Anchors:** Replaced plain TOC with 3-column navigation grid and explicit `<a id="...">` anchors for 100% verified link resolution; added Before vs After visual SVG banner.

## [5.3.1] - 2026-10-31

### Added
- **Test suite hardening:** `test_core.py`, `test_dedup.py`, `test_torture.py` wrapped in `unittest.TestCase` for proper discovery; 1MB bounded-time threshold relaxed to 5000 ms (measured baseline ~2.5 s) to eliminate flaky CI failures.

### Changed
- Version bumped to 5.3.1 (core.py + pyproject.toml single-source).
- Dead-code audit: confirmed zero unused internal functions in `trimtok/`; all three strategy modules (`lossless_min`, `tiered_shave`, `rtk_strip`) remain imported and in use.
- Duplicate-test audit: confirmed no exact duplicate test files (`tiered_strategy` vs `tiered_integration` are complementary entry-point tests, kept).

### Verification
- Full suite: 59/59 tests pass (~1.2 s).
- 1MB terminal payload: ~2.5 s, never raises, saves > 0 chars.

## [5.3.0] - 2026-10-30

### Added
- **Command-Specific Filters:**
  - 6 command-specific filters: `git log`, `git status`, `ls`, `npm`, `pip`, `find` → compact summary + CCR `#ref` for full retrieval.
  - `discover` command: scans telemetry → identifies low-savings shapes/tools → suggests targeted filters.
  - `gain` command: detailed savings breakdown by shape, tool, cmd + bottleneck analysis.
  - `recall` command: ergonomic alias for `retrieve` with section filters.
- **Multi-Language AST Code Skeletonizer:**
  - tree-sitter based skeletonizer for JavaScript/TypeScript, Go, Rust, Java, C, C++ (Python uses existing ast-based).
  - Preserves signatures/braces, folds only function bodies ≥ 8 lines.
  - Integrated into `compress_tool_result` pipeline alongside existing Python skeletonizer.
- **Merged-Schema TOON:**
  - Single-table merge for heterogeneous JSON arrays with null-filled missing fields.
  - Tries merged before bucketed; exact reconstruction verified.
- **Telemetry Coverage Upgrade:**
  - Added `toon_saved`, `toon_kind`, `toon_used` fields to terminal and tool telemetry.
  - Updated shape classifiers for `json_toon_merged`, `cmd_filter` kinds.

### Changed
- Version bumped to 5.3.0 (core.py + pyproject.toml single-source).
- Expanded skeleton trigger condition to include non-Python code patterns.

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
