# trimtok heavy stress report — 2026-10-12 (post-fix rerun)

Version under test: `trimtok 0.5.2` + 7 weakness fixes landed
(W14/W2 rails, W9 budget, W10 sentinel, W-D10 JSON tail, W7 short-ts,
W4 intra-fold, W1 partial gate). Pre-fix baseline: 48.4% aggregate, SIM 30.0%.
Harness: `tests/stress_heavy.py` (stdlib only, run: `python tests/stress_heavy.py`).
Method: `(before_chars - after_chars) / before_chars`; est-tokens = chars / 4
(`ponytail:` rough 4char/token; use a real tokenizer if pricing matters).
Latency: per-case `ms` from plugin stats or wall clock; medians in `tests/test_benchmark.py`.

## Baseline (post-fix, all green)

| Suite | Result |
|---|---|
| `tests/test_core.py` | PASS, 0 failures (incl. rails-hit/miss, thai-mid, sentinel FP, overflow-partial) |
| `tests/test_dedup.py` | PASS, 0 failures (incl. budget-word-boundary, minify-tail, env-tail-folds, volatile-near-dup, intra) |
| `tests/test_benchmark.py` | all PASS; terminal 4.4KB median ~1ms; tool first-seen ~1.2ms; cross-turn fold ~1.2ms; CCR put ~2ms / get ~0.36ms |

## Terminal compression (Phase 1) — post-fix

| ID | Case | Before → After | Saved | Gate | ms | Note |
|---|---|---|---|---|---|---|
| T1 | 5k heartbeat w/ varying timestamps | 234,999 → 210 | 99.9% | ok | 44.5 | repeat fold |
| T2 | 2k pytest + 1 error + summary tail | 56,108 → 5,853 | 89.6% | ok | 34.2 | error + tail kept |
| T3 | npm noise x1600 | 44,500 → 8,516 | 80.9% | ok | 22.3 | noise filter |
| T4 | git diff 800 files | 46,760 → 3,380 | 92.8% | ok | 46.1 | kind=diff budget 200 |
| T5 | grep 2k hits (budget 80) | 98,900 → 3,998 | 96.0% | ok | 30.5 | 83 lines out |
| T6 | ls 2k files (budget 60) | 25,999 → 953 | 96.3% | ok | 24.8 | |
| T7 | ANSI 2k colored lines | 57,509 → 3,354 | 94.2% | ok | 9.5 | escape-free verified |
| T8 | spinner + % 2.4k | 55,590 → 1,642 | 97.0% | ok | 46.5 | progress filter |
| T9 | 3k all-error lines | 141,779 → 9,200 | 93.5% | ok partial | 33.7 | was 0% reverted_quality (W1 fixed) |
| T10 | 3k unique lines | 110,390 → 4,907 | 95.6% | ok | 61.5 | lossy window, 2,881 dropped (W5, by design + CCR) |
| T11 | 2k Thai lines + Thai error tail | 58,422 → 7,178 | 87.7% | ok | 44.4 | Thai rail now keeps mid-doc errors too |
| T12 | traceback x8 (4,817 lines) | 94,656 → 11,216 | 88.2% | ok partial | 44.3 | was 0% reverted_quality (W1 fixed) |
| T13 | KEEP line under 1k window | 29,900 → 3,720 | 87.6% | ok | 14.8 | TRIMTOK_KEEP survives |
| T14 | 500KB repeat blob | 500,000 → 236 | 100.0% | ok | 48.6 | headroom excellent |
| T15 | error inside repeat run | 3,322 → 1,730 | 47.9% | ok | 1.3 | error survives fold |

## Tool-result compression (Phase 2) — post-fix

| ID | Case | Before → After | Saved | Gate | ms | Note |
|---|---|---|---|---|---|---|
| D1 | pretty JSON 200 items | 26,799 → 20,046 | 25.2% | ok | 9.9 | minify saved 6,808 |
| D2a | 500 uniform rows, TOON off | 20,280 → 17,336 | 14.5% | ok | 4.8 | |
| D2b | 500 uniform rows, TOON on | 20,280 → 8,978 | 55.7% | ok | 9.7 | 3.8× better than off |
| D3 | cross-turn seed → fold → fold3 | 5,430 → 4,475 | 17.6% | ok (turns 2-3) | 0.2 | turn 1 reverted_gate 0% by design (W3) |
| D5a | read_file 47KB | verbatim | 0% | passthrough_reference | — | by design |
| D5b | search folds vs prior read | 47,500 → 1,099 | 97.7% | ok | 2.0 | best cross-turn case |
| D6 | trailing-ws + blank runs | 31,500 → 26,501 | 15.9% | hygiene | — | saved 4,999 |
| D7 | 500 UUID/sess/ts lines | 50,389 → 19,889 | 60.5% | volatile | — | saved 30,500; +short-ts (W7) |
| D8 | exact re-run | 0% | 0% | passthrough_repeat | — | by design (agent wants full) |
| D9 | 30 long lines repeat | 4,130 → 3,264 | 21.0% | ok | 0.1 | span-level fold |
| D10 | JSON envelope + tail, same inner 80 lines | 7,323 → 5,224 | 28.7% | ok | 0.7 | was 0% reverted (W-D10 fixed) |
| D11 | unique 300 lines | 12,084 → 12,084 | 0% | reverted_gate | 1.1 | no inflate, correct |
| D12 | 1MB repeat blob via tool | 1,000,000 → 167 | 100.0% | ok | 28.4 | was 0% reverted (W4 fixed) |
| D13 | mask expiry keep=2 | 2 turns expired | — | — | — | old stops folding, verified |
| NEW | near-dup HH:MM:SS timestamps | 60-line logs | ok, saved 1,735 | ok | — | was reverted_gate (W7 fixed) |

## CCR + retrieve

| ID | Case | Result |
|---|---|---|
| C1 | 100KB put + get roundtrip | exact, put 5.6ms + get 27.5ms cold (benchmark warm median: put 1.77ms / get 0.31ms) |
| C2 | paginated slice vs full 100KB | 100,000 → 37 chars (2 lines), correct `offset/limit` |
| C3 | grep no-match | `matches_count == 0`, correct |

## Realistic 5-turn session sim

| Turn | Before → After | Saved | Gate |
|---|---|---|---|
| read skill 30KB | 30,000 → 30,000 | 0% | passthrough_reference |
| search1 15.7KB (first-seen) | 0% | 0% | reverted_gate |
| search2 overlap 17.7KB | 0% | 0% | reverted_gate |
| pytest 30KB | 30,417 → 5,391 | 82.3% | ok |
| json 12KB TOON | 12,080 → 5,377 | 55.5% | ok |
| **SIM TOTAL** | **105,839 → 74,110** | **30.0%, ~7,932 est-tokens** | |

Aggregate over every stress case: 3,059,792 → 359,481 chars = **88.3%, ~675,078 est-tokens** (was 48.4% / ~370,590 pre-fix).

## Weakness log — all 7 code fixes landed + verified this session

### W1 — quality-gate was all-or-nothing on error-dense output (HIGH, FIXED)
Was: T9 (3,000 forced > budget 200, missing 2,799) and T12 (2,408 forced, 101 missing) both reverted to **0%**.
Fix: `core.py` quality-gate v2 — when forced lines overflow the budget, emit the windowed output as `ok` with `partial=True` + `omitted_errors` count (gap markers + head hidden count report the omission; full original in CCR). Non-overflow missing still reverts.
Now: T9 93.5%, T12 88.2%.

### W14 — error rails miss real failure shapes (HIGH, safety-relevant)
Evidence: `is_important() == False` for `errors: 3`, `AssertionError: x != y`, `Segmentation fault (core dumped)`, `exit code 1`, `EPIPE`, `OOM ...`, Thai `ผลลัพธ์ผิดพลาด`. Regex uses `\bassertion\b` (never matches `AssertionError`) and `segfault` (never matches `segmentation fault`); no plural `errors`, no exit-code/OOM shapes.
Root: `core.py` `_ERROR_RE` / `_ERROR_HINTS`.
Next: extend rails (plural, camelCase asserts, multiword segfault, exit-code, OOM/EPIPE, Thai keywords) + regression tests. One-line-class fix.

### W4 — tool path has no intra-document repeat fold (HIGH, biggest single-case loss)
Evidence: identical 1MB repeat blob — terminal saves 99.97% (1,000,000 → 256), tool saves **0%** (`reverted_gate`).
Root: `dedup.py` folds only against *prior* corpus; a first-seen repetitive document never folds against itself, then the token-gate reverts.
Next: run repeat-fold inside the single tool result before the token-gate (reuse the terminal `_fold_repeats` shape).

### W-D10 — JSON + trailing tail breaks minify AND inner indexing (MEDIUM-HIGH)
Evidence: `{"...80 inner lines..."} + "\n" + "p"*2100` → `reverted_gate`, corpus indexes only 2 lines, sample inner line not indexed.
Root: `minify_json` is whole-doc only (tail kills it) and `remember()` skips inner indexing when `json.loads(text)` fails on the tail.
Next: strip/split trailing non-JSON tail before minify + index inner lines from the longest JSON prefix.

### W9 — command-budget substring matching (MEDIUM)
Evidence: `_budget_for("cat diffutils.txt", "text", 120) == 200`; `"false"` contains `"ls"`, `"contest"` contains `"test"`.
Root: `core.py` `_budget_for` uses `verb in cmd`.
Next: match on command verb / word boundaries instead of substring.

### W7 — tool near-dup timestamps don't normalize (MEDIUM)
Evidence: 60-line logs differing only by `10:00:xx` → `10:01:xx` fold via terminal (saves 9,788) but tool `reverted_gate` 0%.
Root: `normalize_volatile` handles long `YYYY-MM-DD HH:MM` / UUID / sess-id only, not short `HH:MM:SS`.
Next: add short-time normalization to the dedup path.

### W3 — first-seen seed always 0% (MEDIUM, by design but dominates sim)
Evidence: D3 turn 1 `reverted_gate`; sim wastes 33KB across search1/search2 before any fold lands.
Root: keep-earliest + fold-after-remember means the first copy pays full price plus head cost on retry.
Next: lossless-only first-seen path (hygiene + volatile + minify, smaller/no head when nothing folded).

### W5 — terminal unique-content window is lossy (MEDIUM, recoverable)
Evidence: T10 drops 2,881 lines with `[... N lines omitted ...]`; only CCR holds the original.
Note: tool path correctly refuses (`reverted_gate`) on the same shape. Terminal trades completeness for ratio — acceptable only because CCR retrieval is exact; surface this in status output.

### W2 — Thai error rails absent (LOW-MEDIUM)
Evidence: `is_important("ข้อผิดพลาด: ...") == False`; mid-document Thai error dropped while tail Thai error survived by position, not by rail.
Next: add Thai failure keywords alongside W14.

### W10 — sentinel false-positive proven (LOW, fail-open)
Evidence: crafted legit log with `[tt:note ` inside the first 2,000 chars → `passthrough_sentinel` on both terminal and tool paths.
Root: `SENTINEL_RE = \[tt:[a-z]+\b` matches any mention, head-window only.
Next: require full marker shape (`[tt:<kind> ... retrieve:<hex>]`).

### D2 — TOON default-off leaves 3.8× on the table (DECISION for P Choke)
Evidence: 500 uniform rows — off 14.5% vs on 55.7%.
Next: flip `toon_enabled` default only with explicit approval (behavior change on JSON tool output).

## What held up (do not regress)

Heartbeat/spinner/ANSI/noise 80–100%; grep/ls budgets 96%; KEEP prefix; idempotency; never-inflate both paths; determinism; 500KB in 24ms; CCR exact roundtrip; paginated/grep retrieve; mask expiry evicts and stops old folds; span/line budget eviction; full-corpus (200 spans) query 0.6ms.

## Repro

```bash
python tests/test_core.py
python tests/test_dedup.py
python tests/test_benchmark.py
python tests/stress_heavy.py
```

## Prioritized fix order (smallest diff first)

1. W14 error-rail gaps (regex/hints + tests).
2. W9 budget word-boundary.
3. W10 sentinel shape tightening.
4. W-D10 JSON-prefix minify/index.
5. W7 short-time normalization.
6. W4 intra-document tool fold.
7. W1 partial-keep quality gate (design + tests; largest change, keep last).
8. W2 Thai rails (bundle with 1). D2 default flip only on approval.
