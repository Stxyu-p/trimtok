# trimtok baseline snapshot — v0.5.2 (pre-expansion, 2026-10-12)

Purpose: freeze today's capability numbers so the post-expansion re-measure
(Drain templates, shingle near-dedup, HEAD_TAIL, guardrails, arch deepening)
can be compared case-by-case. All figures re-measured this session unless noted.
Method: chars; est-tokens = chars/4 rough (`ponytail:` use a real tokenizer if
pricing matters). Env: Python 3.14.7, Windows.

## Version / tree

- `VERSION = 0.5.2` (`core.py`), `version: 0.5.2` (`plugin.yaml`)
- Code: `__init__.py` 453 / `ccr.py` 167 / `core.py` 309 / `dedup.py` 680 lines
- Tree state: MODIFIED uncommitted (`core.py`, `dedup.py`, `plugin.yaml`,
  `tests/test_core.py`, `tests/test_dedup.py`) + untracked (`tests/stress_heavy.py`,
  `tests/STRESS_REPORT.md`). HEAD `54e594c`.
- Hooks: `transform_terminal_output`, `transform_tool_result`, `post_llm_call`,
  `on_session_start`, `on_session_end`. Tool: `trimtok_retrieve`
  (hash + offset/limit + grep).

## Suites (green)

| Suite | Result |
|---|---|
| `tests/test_core.py` | 53 PASS, 0 failures |
| `tests/test_dedup.py` | 62 PASS, 0 failures |
| `tests/test_benchmark.py` | all PASS |

## Latency (benchmark medians, n=50)

| Path | Median | p95 |
|---|---|---|
| terminal 4.4KB repeat | 1.06ms | 1.37ms |
| tool first-seen pretty JSON | 0.95ms | 2.23ms |
| tool exact-repeat passthrough | 0.04ms | 0.08ms |
| tool cross-turn fold 60 lines | 0.54ms | 1.06ms |
| tool no-gain 9.5KB | 0.55ms | 1.36ms |
| CCR put unique | 2.11ms | 4.38ms |
| CCR get repeat | 0.44ms | 0.83ms |

Stress wall-clock spot checks: T9 33.7ms, T12 44.3ms, 500KB terminal 48.6ms,
1MB tool 28.4ms (fresh session; benchmark medians are the gate, not these).

## Token reduction (stress_heavy.py, same fixtures as vendor compare)

| ID | Before → After | Cut | Gate |
|---|---|---|---|
| T1 heartbeat 5k | 234,999 → 210 | 99.9% | ok |
| T2 pytest 2k+1err | 56,108 → 5,853 | 89.6% | ok |
| T3 npm noise | 44,500 → 8,516 | 80.9% | ok |
| T4 git diff 800 | 46,760 → 3,380 | 92.8% | ok |
| T5 grep 2k b80 | 98,900 → 3,998 | 96.0% | ok |
| T6 ls 2k b60 | 25,999 → 953 | 96.3% | ok |
| T7 ANSI 2k | 57,509 → 3,354 | 94.2% | ok |
| T8 spinner 2.4k | 55,590 → 1,642 | 97.0% | ok |
| T9 err 3k | 141,779 → 9,200 | 93.5% | ok partial |
| T10 unique 3k | 110,390 → 4,907 | 95.6% | ok (lossy window, CCR recovers) |
| T11 Thai 2k | 58,422 → 7,178 | 87.7% | ok |
| T12 trace x8 | 94,656 → 11,216 | 88.2% | ok partial |
| T13 KEEP | 29,900 → 3,720 | 87.6% | ok |
| T14 500KB | 500,000 → 236 | 100.0% | ok |
| T15 err-in-repeat | 3,322 → 1,730 | 47.9% | ok |
| D1 pretty JSON | 26,799 → 20,046 | 25.2% | ok |
| D2a TOON off | 20,280 → 17,336 | 14.5% | ok |
| D2b TOON on | 20,280 → 8,978 | 55.7% | ok |
| D3 cross-turn | 5,430 → 4,475 | 17.6% | ok (turns 2-3; turn 1 0% by design) |
| D5b vs read | 47,500 → 1,099 | 97.7% | ok |
| D6 hygiene | 31,500 → 26,501 | 15.9% | hygiene |
| D7 volatile 500 | 50,389 → 19,889 | 60.5% | volatile |
| D9 30 lines | 4,130 → 3,264 | 21.0% | ok |
| D10 env+tail | 7,337 → 5,224 | 28.8% | ok |
| D12 1MB | 1,000,000 → 167 | 100.0% | ok |
| SIM 5-turn | 105,839 → 74,110 | 30.0% (~7,932 tok) | mixed |
| ALL cases | 3,059,792 → 359,481 | 88.3% (~675,078 tok) | — |

## Machine resources (tracemalloc, this session)

| Item | Figure |
|---|---|
| terminal 500KB peak | 2,861KB (settled 720KB) |
| tool 1MB peak | 2,856KB (settled 978KB) |
| corpus 200 spans / 200 lines | 288KB peak (124,900 span chars) |
| CCR 100 entries disk | 51KB, 2.40ms/entry |
| deps | stdlib only, zero third-party |

## Error behavior (fail-open, never raises)

- `compress_terminal` / `compress_tool_result` / CCR return input (or `""`/`None`
  for put/get) on any exception — verified by `transform-fails-open-without-ccr`,
  `tool-result-fails-open-without-ccr` tests.
- Error rails: 36/36 (`rails-hit/miss`); Thai mid-doc kept by rail.
- Non-overflow missing-error still reverts (`reverted_quality`); overflow emits
  `partial=True` + `omitted_errors` count.
- Unique tool docs revert (`reverted_gate`), never inflate.

## Gates inventory

`ok` (+`partial`), `passthrough_small` (<2048 chars), `passthrough_sentinel`,
`passthrough_non_str`, `passthrough_reference` (read_file), `passthrough_repeat`,
`reverted_gate`, `reverted_quality`, `hygiene`, `volatile`.

## Config defaults / exclusions

All defaults from `plugin.yaml`: enabled, phase2, mask(keep 2), audit on;
TOON **off**; min_chars 2048; max_lines 120; CCR 1000 entries / 50MB / 24h TTL.
`exclude_commands` / `exclude_tools` default empty.

## Known by-design 0% (not gaps)

W3 first-seen unique seed, read_file verbatim, exact re-run, small inputs,
JSON-looking tails untouched by minify.

## Pending decisions

TOON default flip (3.8× left on table) — needs explicit approval.
W5 terminal window lossy by design + CCR. W3 closed with `firstseen-unique-full` test.

## Repro

```bash
python tests/test_core.py
python tests/test_dedup.py
python tests/test_benchmark.py
python tests/stress_heavy.py
```

## Usage log (shipped 2026-10-12, Proposal 1)

`usage.jsonl` (+`.1` rotation, 1MB cap, `usage_enabled` default on) records every
hook invocation — ok, revert, passthrough — with gate + shape + ms + hash, plus
retrieve hit/miss (full/slice/grep). `/trimtok usage` aggregates: event/fold
counts, gate + shape breakdown, top-saving commands, retrieve hit rate.
Shape labels derive from flags the fold already computed (no second opinion);
no content or prompt text is ever logged (commands truncated to 120 chars).
Live-use numbers will be appended here after 1 week of real sessions.

## Expansion (v0.6.0, 2026-10-12)

Shipped same-day per P Choke's "ทำทั้งหมด" approval, TDD (red→green each):

| Feature | Source idea | Where |
|---|---|---|
| `marker.py` module (one home for `[tt:…]` shape, `is_exempt`) | arch report C1 (Strong) | marker.py + 6 call sites |
| Drain template `×N` (scattered non-adjacent same-shape) | squeez `log_template.rs` | core `_fold_templates` |
| HEAD_TAIL stub ≥100KB unique tool docs | lossless-compact KEEP_HEAD_TAIL | dedup `_fold_headtail` |
| Shingle near-dedup (3-word, Jaccard≥0.8, line level) | claw-compactor semantic_dedup | dedup `_fold_shingles` (level 3 in fold_spans) |
| Exemption list (ports/hashes/counts never fold) | LLMLingua-2 force_reserve | marker `is_exempt` @ 4 fold sites |
| 500-token min gate | compressmcp | existing min_chars=2048 (512 tok) — pinned by test |
| TOON strict key-order | squeez `toon.rs` | dedup `toon_encode` |
| Hook pipeline merge (`_finalize`) | arch report C3 (Worth exploring) | `__init__` |

Post-expansion stress: 3,059,792 → 347,587 chars = **88.6%** (~678,051 est-tokens;
was 88.3% pre-expansion). Suites: core 64 PASS / dedup 75 PASS / benchmark green.
Deferred by design: arch C2 fold-module + C4 normalize-module unification
(pure refactors — Drain+shingle already deliver the functional value;
revisit when folds next change), arch C5 remember-split (Speculative).

Pitfall log (do not repeat): `:\d{2,5}` exemption matched `20:00:01`
timestamps → all folds dead (needed `(?<!\d)`); shingle fixture needed
template-identical lines (counter-everywhere = Jaccard 0.04, spec is
line-level not block-level for trimtok); `_fold_shingle_blocks` was dead
code (span path always wins first) — deleted.
