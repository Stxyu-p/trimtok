# trimtok baseline snapshot — v0.6.2 (post-incremental, 2026-10-14)

Purpose: freeze post-Step-1..5 numbers so future work compares case-by-case.
Base: v0.6.1 HEAD `de19571` + 5-file patch (benchmark fixture, estimator,
signature-rail revert note, agent-payload glue). Method: chars;
est-tokens = chars/4 rough (kept for history) + est-tokens-byteclass
side-by-side below. Env: Python 3.14.7, Windows.

## Version / tree

- `VERSION = 0.6.1` (`core.py`), `version: 0.6.1` (`plugin.yaml`) — code is
  0.6.1 + uncommitted Step-1..5 patch; version bump to 0.6.2 on merge.
- Changed: `__init__.py` (+37 glue), `core.py` (+8 est fields), `dedup.py`
  (+10 import/stats/revert note), `marker.py` (+27 estimator),
  `tests/test_benchmark.py` (+20 Case-A fixture).
- Hooks unchanged: `transform_terminal_output`, `transform_tool_result`,
  `post_llm_call`, `on_session_start`, `on_session_end`.
  Tool: `trimtok_retrieve` (hash + offset/limit + grep).

## Suites (green, this session)

| Suite | Result |
|---|---|
| `tests/test_core.py` | 64 PASS, 0 failures |
| `tests/test_dedup.py` | 77 PASS, 0 failures |
| `tests/test_torture.py` | 18 PASS, 0 failures |
| `tests/test_benchmark.py` | all PASS (fixed `_no_gain_sample`) |

## Latency (benchmark medians, n=50, this session)

| Path | Median | p95 | Budget |
|---|---|---|---|
| terminal 4.4KB repeat | 0.82ms | 1.27ms | <10ms |
| tool first-seen pretty JSON | 3.92ms | 5.55ms | <10ms |
| tool exact-repeat passthrough | 0.06ms | 0.13ms | <10ms |
| tool cross-turn fold 60 lines | 1.52ms | 3.43ms | <10ms |
| tool no-gain 13.5KB (new fixture) | 2.35ms | 5.48ms | <10ms |
| CCR put unique | 2.00ms | 4.44ms | <5ms |
| CCR get repeat | 0.26ms | 0.33ms | <5ms |

Note: first-seen 1.01 -> 3.92ms includes the new `estimate_tokens` stats
fields (2× findall over the full doc) + machine noise. Still well inside
the 10ms budget. If it ever nears budget, compute est fields lazily.

## Token reduction (stress_heavy.py, same fixtures)

Aggregate: 3,059,792 -> 324,346 chars = **89.4%** (~683,862 est-tokens).
0.6.0 measured 88.6% (347,587) — delta +0.8pp is fixture noise across
runs (same gates, T-case wall-clock variance), NOT a claimed gain.

SIM 5-turn: 105,839 -> 41,092 = **61.2%** (~16,187 tok) — unchanged,
lock this number.

| ID | Before → After | Cut | Gate |
|---|---|---|---|
| T1 heartbeat 5k | 234,999 → 210 | 99.9% | ok |
| T2 pytest 2k+1err | 56,108 → 298 | 99.5% | ok |
| T3 npm noise | 44,500 → 5,473 | 87.7% | ok |
| T4 git diff 800 | 46,760 → 2,096 | 95.5% | ok |
| T5 grep 2k b80 | 98,900 → 3,987 | 96.0% | ok |
| T6 ls 2k b60 | 25,999 → 953 | 96.3% | ok |
| T7 ANSI 2k | 57,509 → 3,354 | 94.2% | ok |
| T8 spinner 2.4k | 55,590 → 1,741 | 96.9% | ok |
| T9 err 3k | 141,779 → 9,200 | 93.5% | ok |
| T10 unique 3k | 110,390 → 4,907 | 95.6% | ok |
| T11 Thai 2k | 58,422 → 1,720 | 97.1% | ok |
| T12 trace x8 | 94,656 → 11,216 | 88.2% | ok |
| T13 KEEP | 29,900 → 228 | 99.2% | ok |
| T14 500KB | 500,000 → 236 | 100.0% | ok |
| T15 err-in-repeat | 3,322 → 1,730 | 47.9% | ok |
| D1 pretty JSON | 26,799 → 20,046 | 25.2% | ok |
| D2a TOON off | 20,280 → 17,336 | 14.5% | ok |
| D2b TOON on | 20,280 → 8,978 | 55.7% | ok |
| D3 cross-turn | 5,430 → 2,353/4,475 | 56.7/17.6% | ok |
| D5a read verbatim | 47,500 → 47,500 | 0.0% | passthrough_reference |
| D5b vs read | 47,500 → 1,099 | 97.7% | ok |
| D6 hygiene | 31,500 → 26,501 | 15.9% | hygiene |
| D7 volatile 500 | 50,389 → 19,889 | 60.5% | volatile |
| D8 repeat | 5,430 → 5,430 | 0.0% | passthrough_repeat |
| D9 30 lines | 4,130 → 2,267/3,264 | 45.1/21.0% | ok |
| D10 env+tail | 7,323 → 7,323/5,224 | 0.0/28.7% | reverted_gate/ok |
| D11 unique | 12,084 → 633 | 94.8% | ok |
| D12 1MB | 1,000,000 → 167 | 100.0% | ok |
| SIM 5-turn | 105,839 → 41,092 | 61.2% | mixed |

## Estimator side-by-side (new, stats-only)

`estimate_tokens()` in `marker.py`, reported as `est_tokens_before/after`
in every `ok` stats dict. Gates still compare `len()` only.

| Sample | chars//4 | byteclass est | Cost |
|---|---|---|---|
| Thai 4,200 chars | 1,050 | 3,520 | ~1.2ms |
| code 4,500 chars | 1,125 | 1,666 | ~0.3ms |
| EN 5,100 chars | 1,275 | 1,275 | ~0.03ms |
| 1MB ASCII | 250,000 | 115,000 | ~2.7ms |

## Error behavior (fail-open, never raises)

- `compress_terminal` / `compress_tool_result` / CCR return input (or
  `""`/`None` for put/get) on any exception — torture suite 18/18.
- Error rails: EN+Thai kept verbatim (T2/T9/T11/T15 PASS).
- Non-overflow missing-error still reverts (`reverted_quality`); overflow
  emits `partial=True` + `omitted_errors` count.
- Unique tool docs revert (`reverted_gate`), never inflate.

## Gates inventory

`ok` (+`partial`), `passthrough_small` (<2048 chars),
`passthrough_sentinel`, `passthrough_non_str`, `passthrough_reference`
(read_file), `passthrough_repeat`, `reverted_gate`, `reverted_quality`,
`hygiene`, `volatile`.

## Config defaults / exclusions

All defaults from `plugin.yaml`: enabled, phase2, mask(keep 2), audit on;
TOON **off**; min_chars 2048; max_lines 120; CCR 1000 entries / 50MB / 24h
TTL. `exclude_commands` / `exclude_tools` default empty.

## Known by-design 0% (not gaps)

First-seen unique seed, read_file verbatim (D5a), exact re-run, small
inputs, JSON-looking tails untouched by minify.

## Step 4 outcome (signature rail: TRIED then REVERTED)

- Tried: `_SIG_RE` pinning def/class/function/func lines verbatim inside
  `_fold_intra_templates` — all 120/120 signature lines kept in probe.
- Measured: SIM 5-turn 61.2% -> 30.0% (-31.2pp); the 800 SIM search sig
  lines that Drain templates used to fold stayed verbatim.
- Verdict per kill-criteria: REVERTED, code removed, note left in
  `dedup.py`. One-line `path:line:def` hits fold by design; naked sigs
  without digits never group anyway. No Step-4 gain shipped.

## Repro

```bash
python tests/test_core.py
python tests/test_dedup.py
python tests/test_torture.py
python tests/test_benchmark.py
python tests/stress_heavy.py
```
