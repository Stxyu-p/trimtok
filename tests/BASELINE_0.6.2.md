# trimtok baseline snapshot — v0.6.2 (round-1 final, 2026-10-15)

Purpose: freeze the pre-Phase-1 (Trimtok 2.0) state so the v2 phase work is
measured case-by-case. All numbers measured 2026-10-15 **before** the Phase-1
edits, in two windows because this box's short-run medians vary ~2x with
ambient load: "quiet" (background run, ~19:20) and "loaded" (interactive
session, ~21:00). Python 3.14.7, Windows.

## Version / tree

- `VERSION = 0.6.2` (`core.py`), `version: 0.6.2` (`plugin.yaml`),
  HEAD `f8c0ca1` ("feat(release): bump v0.6.2 …") on `main`, clean tree.
- Round-1 content in tree: polarity-locked guard (`marker.is_important`
  shared into `dedup.py` folds), CCR in-memory LRU (`ccr.py`), plus the
  benchmark fixture and step-1..5 patch (finished 2026-10-14).

## Suites (green this session)

| Suite | Result |
|---|---|
| `tests/test_core.py` | 64 PASS, 0 failures (pre-change) |
| `tests/test_dedup.py` | 77 PASS, 0 failures |
| `tests/test_torture.py` | 18 PASS, 0 failures |
| `tests/test_benchmark.py` | all assertions pass |
| `tests/stress_heavy.py` | fails=0 |

## Latency (benchmark medians, n=50; ms)

| Path | quiet med / p95 | loaded med / p95 |
|---|---|---|
| terminal 4.4 KB repeat | 0.59 / 0.96 | 1.59 / 2.86 |
| tool first-seen pretty JSON | 1.85 / 2.79 | 3.67 / 5.65 |
| tool exact-repeat passthrough | 0.03 / 0.04 | 0.09 / 0.24 |
| tool cross-turn fold 60 lines | 1.79 / 6.73 | 3.12 / 5.34 |
| tool no-gain ~9.5 KB | 2.47 / 4.67 | 5.46 / 8.20 |
| CCR put unique | 1.93 / 6.19 | 1.99 / 6.50 |
| CCR get repeat | 0.11 / 0.14 | 0.20 / 0.27 |

Shapes the Phase-1 guard design: a 31 KB terminal compress already takes
~25–55 ms on a loaded box (both versions), so any hard budget must be a
safety ceiling, not a latency target.

## Token reduction (stress_heavy.py, same fixtures)

- SIM 5-turn: 105,839 → 41,092 chars = **61.2%** (~16,187 est-tokens).
- ALL CASES: 3,059,792 → 324,346 chars = **89.4%** (~683,862 est-tokens); fails=0.

## Ops / config notes

- Hooks: `transform_terminal_output`, `transform_tool_result`,
  `post_llm_call`, `on_session_start`, `on_session_end`;
  tool: `trimtok_retrieve` (hash + offset/limit + grep).
- Test-env pitfall: an open sqlite connection locks its db file on Windows —
  close the v2 store before `TemporaryDirectory` cleanup.
- `ccr.default_dir` is the single source of truth for the store dir
  (patchable test seam; any v2 store must resolve it dynamically).

## What the next round must reproduce / beat

- Stub pointer size ~-70% (plan), CCR write < 0.25 ms, alias **and** sha256
  retrieval byte-equal 100%, fail-open guard behavior; no regression on the
  numbers above.
