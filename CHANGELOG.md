# Changelog

All notable changes to trimtok are documented here.

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
