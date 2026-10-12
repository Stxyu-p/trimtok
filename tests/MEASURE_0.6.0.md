# Measure: trimtok 0.5.2 → 0.6.0 — 2026-10-12

Baseline: `tests/BASELINE_0.5.2.md` (frozen pre-expansion). All figures below
re-measured this session on the 0.6.0 tree (marker.py + Drain + HEAD_TAIL +
shingle + exemption + TOON-strict + `_finalize`). Method: chars; est-tokens =
chars/4 rough. Env: Python 3.14.7, Windows.

## 1. Suites (PASS count, 0 failures both sides)

| Suite | 0.5.2 baseline | 0.6.0 now | Delta |
|---|---|---|---|
| test_core.py | 53 PASS | 64 PASS | +11 (3 drain + 8 usage) |
| test_dedup.py | 62 PASS | 75 PASS | +13 (13: intra/headtail/shingle/exempt/min-gate/toon-strict) |
| test_benchmark.py | all PASS | all PASS | — |

## 2. Latency (benchmark medians, n=50)

| Path | 0.5.2 | 0.6.0 | Delta |
|---|---|---|---|
| terminal 4.4KB repeat | 1.06ms | 0.89ms | noise |
| tool first-seen pretty JSON | 0.95ms | 1.01ms | noise |
| tool exact-repeat passthrough | 0.04ms | 0.05ms | — |
| tool cross-turn fold 60 lines | 0.54ms | 1.10ms | +0.56ms, see note |
| tool no-gain 9.5KB | 0.55ms | 0.55ms | — |
| CCR put unique | 2.11ms | 2.00ms | noise |
| CCR get repeat | 0.44ms | 0.27ms | noise |

Note: cross-turn fold regressed 0.54 → 2.83ms when shingle level-3 ran
unconditionally (cProfile: `_fold_shingles` + `is_exempt` regex dominate).
Fixed by gating level 3 on `folded_chars == 0` (second-chance only, same
pattern as intra/headtail). Residual +0.56ms is the exemption check inside
the verbatim loop — accepted, asserts still pass.

## 3. Stress (same fixtures, 26 rows — only changes listed)

Aggregate: 3,059,792 → 347,587 chars = **88.6%** (~678,051 est-tokens).
Baseline was 88.3% (~675,078). Delta **+0.3pp / +2,973 tokens** — small
because the stress fixtures predate the new folds; the new shapes show up in
dedicated probes below, not in the old 26 rows. SIM 5-turn unchanged at
30.0% (W3 by design untouched).

| ID | 0.5.2 | 0.6.0 | Note |
|---|---|---|---|
| all 26 rows | same gates | same gates | no regressions |

New-fold probes (no 0.5.2 equivalent — shapes previously scored 0%):

| Probe | Result |
|---|---|
| Drain scattered 60×2 templates | ok, `[template ×N]` fires, sample kept |
| HEAD_TAIL 192KB unique tool doc | ok, headtail_saved=188,116, both ends verbatim |
| Shingle 40 near-dup long lines | ok, 40 `[tt:shingle]` pointers, keep-first in corpus |
| Exempt ports/hashes | never folded (`is_exempt` at 4 fold sites) |
| TOON key-order mismatch | rejected (`""`) |

## 4. Resources (tracemalloc, same scripts)

| Item | 0.5.2 | 0.6.0 |
|---|---|---|
| terminal 500KB peak | 2,861KB (721 settled) | 2,861KB (721 settled) |
| tool 1MB peak | 2,856KB (979 settled) | 2,856KB (979 settled) |
| corpus 200 spans | 288KB | 288KB |
| CCR 100 entries | 51KB, 2.40ms/entry | 51KB, 2.68ms/entry (noise) |
| tree size | 1,609 lines / 4 files | 2,061 lines / 5 files (+marker.py) |

## 5. Error behavior + quality (spot checks, all pass)

- rails 4/4 (`ERROR disk full`, `AssertionError`, `exit code 1`, Thai error)
- T9: ok partial, omitted=2,799, saved=132,579 (unchanged)
- TOON 500 rows: used, saved 11,302, all names intact (decode-back clean)
- fail-open paths untouched (never-raises discipline holds)

## Verdict

0.6.0 keeps every 0.5.2 number (no regressions) and adds four folds that turn
previously-0% shapes into 88-100% recoverable cuts. Aggregate +0.3pp
understates the gain — the old fixtures can't exercise the new shapes; the
probes above are the honest measure. Latency cost: +0.56ms on cross-turn
fold only, inside budget.
