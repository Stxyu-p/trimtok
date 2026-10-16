# trimtok baseline snapshot — v0.10.0 (Pre-v3 Evolution, 2026-10-16)

Purpose: Freeze the v0.10.0 baseline across latency, token reduction, resource usage, and error/gates/config before initiating the Trimtok 3.0 upgrade plan. Measured on Windows 11, Python 3.14.7.

## 1. Version & Git Tree

- `VERSION = 0.10.0` (`core.py`), `version: 0.10.0` (`plugin.yaml`)
- Git HEAD: `de5ddbd` on branch `main` (clean working tree)
- Deployed location: `plugins/trimtok` byte-identical to `trimtok/` workspace

## 2. Test Suite Status

| Suite | Status | Checks / Assertions |
| :--- | :--- | :--- |
| `tests/test_core.py` | PASS | 64 checks |
| `tests/test_dedup.py` | PASS | 77 checks |
| `tests/test_torture.py` | PASS | 18 checks |
| `tests/test_v2_phase1.py` | PASS | 12 checks |
| `tests/test_v2_phase2.py` | PASS | 15 checks |
| `tests/test_v2_phase3.py` | PASS | 26 checks |
| `tests/test_v2_phase4.py` | PASS | 28 checks |
| `tests/test_benchmark.py` | PASS | All 9 assertions |
| `tests/stress_heavy.py` | PASS | 34 stress cases, 0 failures |
| **Total Test Checks** | **PASS** | **290 checks / 0 failures** |

## 3. Latency (Benchmark Medians & P95, n=50; ms)

Measured via `python trimtok/tests/test_benchmark.py` on current environment:

| Path / Workload | Median | p95 | Hard Guard Budget |
| :--- | :--- | :--- | :--- |
| Terminal 4.4 KB repeated log | 1.13 ms | 6.52 ms | < 10 ms |
| Tool first-seen pretty JSON (200 rows) | 5.96 ms | 15.24 ms | < 20 ms |
| Tool exact-repeat passthrough | 0.05 ms | 0.15 ms | < 1 ms |
| Tool cross-turn fold (60 shared lines) | 2.20 ms | 3.65 ms | < 10 ms |
| Tool large no-gain input (~9.5 KB) | 3.48 ms | 8.33 ms | < 10 ms |
| CCR Legacy put (unique payload) | 1.95 ms | 4.51 ms | < 5 ms |
| CCR Legacy get (repeated payload) | 0.20 ms | 0.23 ms | < 2 ms |
| CCR v2 SQLite put (unique alias) | 0.22 ms | 0.54 ms | < 1 ms |
| CCR v2 SQLite get (cached alias `#c1`) | 0.007 ms | 0.008 ms | < 0.1 ms |
| Delta step 200-line re-run | 0.23 ms | 0.45 ms | < 1.2 ms |
| Delta step 2,000-line re-run | 3.90 ms | 5.20 ms | < 10 ms |

## 4. Token & Character Reduction (stress_heavy.py)

| Workload Category | Original Chars | Compressed Chars | Reduction (%) | Est. Tokens Saved |
| :--- | :--- | :--- | :--- | :--- |
| T1: Heartbeat log (5k lines) | 234,999 | 224 | 99.9% | ~58,693 |
| T2: Pytest (2k lines + 1 error) | 56,108 | 312 | 99.4% | ~13,949 |
| T3: npm noise logs | 44,500 | 5,487 | 87.7% | ~9,753 |
| T4: Git diff 800 files | 46,760 | 2,110 | 95.5% | ~11,162 |
| T5: Ripgrep 2k lines (b80) | 98,900 | 4,001 | 96.0% | ~23,724 |
| T7: Heavy ANSI colors 2k lines | 57,509 | 3,368 | 94.1% | ~13,535 |
| T9: Error-dense logs (3k lines) | 141,779 | 9,214 | 93.5% | ~33,141 |
| T12: Python traceback (8 frames) | 94,656 | 11,230 | 88.1% | ~20,856 |
| T14: Massive 500 KB log | 500,000 | 250 | 100.0% | ~124,937 |
| D1: Pretty JSON 200 rows | 26,799 | 20,038 | 25.2% (minify only) | ~1,690 |
| D2b: TOON 500 rows (flat) | 20,280 | 7,008 | 65.4% (TOON) | ~3,318 |
| D15b: TOON 1,000 rows (flat) | 101,885 | 26,147 | 74.3% (TOON) | ~18,934 |
| D14: Scoped search grouping | 42,783 | 1,639 | 96.2% | ~10,286 |
| D16: Delta 5-turn pytest loop | 210,725 | 3,136 | 98.5% | ~51,897 |
| **Realistic 5-Turn SIM Session** | **105,839** | **39,904** | **62.3%** | **~16,484** |
| **ALL 34 Stress Cases Combined** | **3,559,894** | **422,408** | **88.1%** | **~784,372** |

## 5. Resource & Memory Footprint

- Process RSS Memory (Python 3.14): ~22.5 MB idle, ~22.6 MB peak during 100KB compression (+0.1 MB delta).
- Network / Socket Connections: 0 (No sockets opened, 0 ports listened).
- Disk Footprint: SQLite database `< 10 MB` rolling cap (auto-purged past 50 MB / 24h TTL).

## 6. Gates & Configuration Baseline

- Fail-open Guard: `guard_ms = 50.0 ms` (any hook exceeding 50 ms safely returns original text).
- Active Hooks: `transform_terminal_output`, `transform_tool_result`, `post_llm_call`, `on_session_start`, `on_session_end`.
- Active Tool: `trimtok_retrieve` (parameters: `hash`, `offset`, `limit`, `grep`).
- Min Chars Threshold: 2,048 chars.
- Terminal Max Lines Budget: 120 lines default (command-specific verbs: diff/log/test 200, grep 80, ls 60, git status 40).
- Delta Engine Threshold: `0.65` line overlap ratio.

## 7. Comparative Baseline: 3-Column Analysis

| Metric / Dimension | Target (เป้าหมาย v3) | Without trimtok (ไม่ใช้) | With trimtok v0.10.0 (ใช้ปัจจุบัน) |
| :--- | :--- | :--- | :--- |
| **Nested JSON Tool Savings** | **65% – 80%** (TOON v2) | 0% (raw JSON floods context) | 25.2% (reverts to plain minify) |
| **JSON Parse Latency (1MB)** | **< 2.0 ms** (via Rust jiter) | 0 ms | 25.0 – 55.0 ms (stdlib json.loads) |
| **Token Measurement Error** | **0.0% Exact** (Rust BPE) | N/A | ±5% to ±15% (regex estimate) |
| **Delta Engine Re-run Match** | **Fuzzy + Shuffled Lines** | 0% (repeats full output) | Exact line-match only (breaks on line#) |
| **Traceback Compression** | **80% – 85%** (Smart Prune) | 0% (full traceback) | 88.1% (blind window, can cut frames) |
| **Git Diff Context Awareness** | **Full Hunk/Scope intact** | 0% (full diff) | 95.5% (blind line windowing) |
| **Retrieval Query Precision** | **Targeted section/errors** | N/A | Full text or manual regex offset |
| **Overall SIM 5-turn Cut** | **> 72.0%** | 0% | 62.3% |
| **Total Test Suite Checks** | **> 360 PASS / 0 FAIL** | N/A | 290 PASS / 0 FAIL |
