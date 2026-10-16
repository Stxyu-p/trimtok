<h1 align="center">trimtok</h1>

<p align="center">
  <strong>Local, stdlib-only context compressor for Hermes Agent. 88.1% fewer characters, every byte recoverable.</strong>
</p>

<p align="center">
  <a href="#performance"><img src="docs/badges/latency.svg" alt="Latency" /></a>
  <a href="#performance"><img src="docs/badges/dependencies.svg" alt="Zero Dependencies" /></a>
  <a href="#safety-rail-ccr"><img src="docs/badges/safety.svg" alt="Fail-open" /></a>
  <a href="#installation"><img src="docs/badges/ecosystem.svg" alt="Hermes Agent" /></a>
  <a href="LICENSE"><img src="docs/badges/license.svg" alt="License" /></a>
</p>

## Overview

trimtok compresses terminal output and tool results on their way to the model: repeated lines collapse to `[repeated Nx]` folds, uniform JSON arrays to lossless TOON tables, and a repeated command to a cross-turn delta of added, removed and still-failing lines. The full original of every compressed result stays in a local content-addressed store and restores with one `trimtok_retrieve` call.

A repeated test command, second run in the same session:

```
[tt:delta cmd="pytest -q" sim=100% +#1 -#1 !#1 #c2 #c1]
--- STILL FAILING / ACTIVE ERRORS (1) ---
! FAILED test_1000.py::test_boom - AssertionError: expected 1 got 2
--- ADDED / CHANGED IN THIS TURN (+1) ---
+ ===== 1 failed, 200 passed in 43.2s =====
--- RESOLVED / REMOVED (-1) ---
- ===== 1 failed, 200 passed in 42.1s =====
```

Measured results (bundled stress harness; full tables under `tests/`):

| Workload | Before → After | Cut |
| :--- | :--- | ---: |
| 1 MB repeated tool output | 1,000,000 → 159 chars | 100.0% |
| 500 KB repeated log | 500,000 → 250 chars | 100.0% |
| 5k-line heartbeat log | 234,999 → 224 chars | 99.9% |
| Repeated pytest loop (5 runs) | 210,725 → 3,136 chars | 98.5% |
| 3k-line error log | 141,779 → 9,214 chars | 93.5% |
| 4.8k-line traceback | 94,656 → 11,230 chars | 88.1% |
| Realistic 5-turn mixed session | 105,839 → 39,904 chars | 62.3% |
| **All 34 stress cases** | **3,559,894 → 422,408 chars** | **88.1%** |

<p align="center">
  <img src="docs/architecture-dark.svg" alt="trimtok architecture" width="100%">
</p>

## Why trimtok

| Guarantee | Detail |
| :--- | :--- |
| Fewer tokens | heavy outputs keep their density; whole logs stop flooding the context window |
| Nothing lost | every fold stores the original; fault lines never fold; persistent failures stay visible |
| Zero dependencies | pure Python standard library; no network; nothing leaves the machine |
| Fail-open | any error, or a guard timeout past 50 ms, returns the original text untouched |

## How it works

Against plain forwarding (no compression layer):

| Dimension | Without trimtok | With trimtok |
| :--- | :--- | :--- |
| 5k-line repeated log | 234,999 chars reach the model | 224 chars plus a retrievable ref |
| Second run of the same command | full output again | delta of added, removed, still-failing lines (98.5% smaller over 5 runs) |
| 1,000-row JSON array | raw JSON | lossless TOON table (74.3% smaller on the 1k-row case) |
| A failing test line | scrolls away in bulk output | kept verbatim by fault rails, always |
| Restoring an original | not available | one `trimtok_retrieve` call, paginated and grep-filtered |

Decision rules:

| Rule | Behavior |
| :--- | :--- |
| Small outputs | under 2 KB (`min_chars`) pass through untouched |
| Fold safety | folds apply only when verifiably smaller; TOON needs a byte-exact round-trip and at least 4 BPE tokens of net gain |
| Fault lines | error, exception, HTTP 5xx and Thai failure lines never fold, at any stage |
| Repeat commands | compared against the previous run in the same session; persistent failures are always re-emitted |
| Retrieval | every compressed result stores its full original, addressed by a short `#ref` alias or digest |

## Modules and layout

| Path | Role | Notes |
| :--- | :--- | :--- |
| `core.py` | terminal pipeline | ANSI strip, repeat and template folds, budgets, never-inflate revert |
| `delta_engine.py` | cross-turn command delta | similarity gate at 0.65, STILL FAILING rail, bounded state |
| `dedup.py` | tool-result pipeline | cross-turn fold, shingle dedup, HEAD_TAIL stubs, TOON wire |
| `scoped_search.py` | search grouping | groups grep hits per file with definition headers |
| `toon_codec.py` | JSON to TOON tables | lossless round-trip gate, token-gain gate |
| `polarity.py` | fault rails | English, Thai and HTTP 5xx signals; memoized |
| `marker.py` | marker format | `[tt:...]` markers, legends, sentinel gate |
| `ccr.py`, `ccr_v2.py` | retrieval store | legacy facade and v2 SQLite alias store |
| `guard.py` | fail-open deadline | hooks revert past `guard_ms` |
| `__init__.py` | hook adapter | 5 hooks, `trimtok_retrieve`, `/trimtok` commands |
| `tests/` | verification | suites, stress harness, benchmarks, baselines |
| `docs/` | assets | architecture SVG, self-hosted badges |

## Safety rail: CCR

Every folded output is stored by content. Address, caps and retrieval filters:

| Property | Value |
| :--- | :--- |
| Address | short alias (`#c1`) or 12-char SHA-256 digest |
| Storage | SQLite index plus local files under `plugin-data/trimtok/` |
| Caps | 1,000 entries, 50 MB, 24 h TTL (LRU eviction) |
| Filters | regex up to 256 chars (no groups), input up to 1 MB, output up to 1,000 matches or 100 KB |

```json
{
  "name": "trimtok_retrieve",
  "arguments": {
    "hash": "#c1",
    "offset": 1,
    "limit": 50,
    "grep": "error|fail"
  }
}
```

## Performance

Median overhead per path, n=50 (`python tests/test_benchmark.py`), against the regression budgets:

| Measurement path | Scenario | Median (measured) | Budget |
| :--- | :--- | ---: | ---: |
| `compress_terminal` | Same 4.4 KB repeated build log | 0.96 ms | < 10 ms |
| Tool result: first-seen | Pretty JSON, fresh session | 9.3 ms | < 25 ms |
| Tool result: exact repeat | Repeat passthrough path | 0.08 ms | < 10 ms |
| Tool result: cross-turn fold | New result folds against a prior turn | 3.6 ms | < 10 ms |
| Tool result: no gain | Unique 9.5 KB input returns unchanged | 8.5 ms | < 10 ms |
| Command delta step | 200-line pytest pair | 0.61 ms | informational |
| CCR put / get | Unique payload put, warm alias get | 2.8 ms / 0.2 ms | < 5 ms |

Large cross-turn deltas (2,000+ lines) add about 5 ms in steady state, dominated by snapshot storage so both refs stay retrievable. The first-seen tool path budgets above its neighbors because it includes a full lossless round-trip verify.

## System footprint

| Property | Value |
| :--- | :--- |
| Language | Python standard library only, zero external packages |
| Hooks and tools | 5 hooks, 1 tool (`trimtok_retrieve`) |
| Store | local SQLite plus files; 1,000 entries, 50 MB, 24 h TTL |
| Session state | fold index 200 spans / 2 MiB / 20k keys; delta engine 32 commands / 24,000 lines / 1.5 MB |
| Logs | `audit.jsonl` and `usage.jsonl`, 1 MB rotation, opt-out |
| Guard profile | default `guard_ms: 50`; the strict deadline profile uses `8.5` |

## Configuration

Settings under `plugins.entries.trimtok.settings` in the Hermes `config.yaml`:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | bool | true | master switch; false = passthrough |
| `guard_ms` | float | 50 | fail-open deadline in ms (1 to 1000) |
| `min_chars` | int | 2048 | minimum output size before compression |
| `max_lines` | int | 120 | terminal line-window budget |
| `phase2_enabled` | bool | true | tool-result pipeline (cross-turn fold) |
| `toon_enabled` | bool | true | lossless TOON tables for uniform JSON |
| `delta_enabled` | bool | true | cross-turn command delta |
| `delta_threshold` | float | 0.65 | delta similarity gate |
| `exclude_commands` | list | [] | command prefixes that bypass compression |
| `exclude_tools` | list | [] | tool names that bypass compression |
| `mask_enabled` / `mask_keep_turns` | bool / int | true / 2 | fold-index freshness mask |
| `audit_enabled` / `usage_enabled` | bool | true | local savings and usage logs |
| `ccr_max_entries` | int | 1000 | store entry cap (LRU) |
| `ccr_max_bytes` | int | 52428800 | store byte budget (50 MB) |
| `ccr_ttl_hours` | int | 24 | original retention window |

### Commands

| Command | What it does |
| :--- | :--- |
| `/trimtok status` | savings and cache entries (cleans expired) |
| `/trimtok usage` | gate and shape breakdown, top-saving commands, retrieve hit rate |
| `/trimtok purge` | evict all cached originals |
| `/trimtok retrieve <hash>` | print a cached original |

## Installation

Clone into the Hermes plugins directory. Windows (PowerShell):

```powershell
git clone https://github.com/Stxyu-p/trimtok.git "$env:LOCALAPPDATA\hermes\plugins\trimtok"
```

Linux / macOS:

```bash
git clone https://github.com/Stxyu-p/trimtok.git ~/.hermes/plugins/trimtok
```

Enable the plugin:

```bash
hermes plugins enable trimtok
```

Quick start:

1. Clone and enable the plugin with the commands above.
2. Use Hermes as usual; heavy outputs compress on their way to the model.
3. Restore an original with `trimtok_retrieve` and its `#ref`, or `/trimtok retrieve <ref>`.

## What is new

**0.10.0** adds the cross-turn command delta engine (STILL FAILING / added / removed), memoized fault classification and guaranteed store-alias refs. Full history: [CHANGELOG.md](CHANGELOG.md).

## Evidence and limitations

| Aspect | Detail |
| :--- | :--- |
| Measurement | bundled harnesses on synthetic fixtures (Python 3.14, Windows); medians of 50 samples; other machines differ by a constant factor |
| Determinism | refused or non-beneficial folds are byte-identical to their input |
| Storage | originals stay on local disk, expire after 24 h, capped at 50 MB |
| Delta latency | 2,000-line loops add about 5 ms in steady state; 200-line pairs measure 0.61 ms |

## Verification

```bash
python tests/test_core.py
python tests/test_dedup.py
python tests/test_v2_phase4.py
python tests/stress_heavy.py
python tests/test_benchmark.py
```

Status: every push passes the full local suite and the isolated Hermes runtime integration test before it lands.

## License

MIT License. Designed and maintained by P Choke & MIKA.
