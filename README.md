<h1 align="center">trimtok</h1>

<p align="center">
  <strong>Local stdlib-only context compressor for Hermes Agent. 88.6% fewer chars on heavy outputs. Every byte recoverable.</strong>
</p>

<p align="center">
  <a href="#benchmarks"><img src="docs/badges/latency.svg" alt="Latency" /></a>
  <a href="#architecture"><img src="docs/badges/dependencies.svg" alt="Zero Dependencies" /></a>
  <a href="#safety-rail-ccr"><img src="docs/badges/safety.svg" alt="Fail-open" /></a>
  <a href="#installation"><img src="docs/badges/ecosystem.svg" alt="Hermes Agent" /></a>
  <a href="LICENSE"><img src="docs/badges/license.svg" alt="License" /></a>
</p>

---

## What it does

Terminal output and tool results pass through folding stages before reaching the model. Repetitive logs collapse to `[repeated Nx]`, scattered same-shape lines to `[template ×N]`, near-duplicates to `[tt:shingle]`, cross-turn repeats to `[tt:seen]` pointers, uniform JSON arrays to TOON tables. Large unique dumps keep head + tail with a stub. The full original is always stored in a local content-addressed cache and retrievable via `trimtok_retrieve`.

| Case | Before → After | Cut |
| :--- | :--- | ---: |
| 1 MB repeated tool output | 1,000,000 → 167 | 100.0% |
| 5k-line heartbeat log | 234,999 → 210 | 99.9% |
| 3k-line error log | 141,779 → 9,200 | 93.5% |
| 4.8k-line traceback | 94,656 → 11,216 | 88.2% |
| JSON envelope + tail | 7,337 → 5,224 | 28.8% |
| Realistic 5-turn session | 105,839 → 41,092 | 61.2% |
| **All 26 stress cases** | **3,059,792 → 324,346** | **89.4%** |

Median overhead ~1 ms per path (terminal 0.6 ms, tool fold 1.4 ms, CCR get 0.1 ms with in-memory LRU). Measured with `python tests/test_benchmark.py` and `python tests/stress_heavy.py`; full tables in `tests/BASELINE_0.6.1.md`.

<p align="center">
  <img src="docs/architecture-dark.svg" alt="trimtok Architecture" width="100%">
</p>

---

## Benchmarks

Median overhead ~1 ms per path; regression budgets below. Run with `python tests/test_benchmark.py` (5 warm-ups, 50 samples, median gate, p95 reported).

| Measurement path | Scenario | Median regression budget |
| :--- | :--- | :--- |
| compress_terminal | Same 4.4 KB repeated build log | < 10 ms |
| Tool result: first-seen | Pretty JSON in a fresh session for every sample | < 10 ms |
| Tool result: exact repeat | Repeated payload reaches the repeat passthrough path | < 10 ms |
| Tool result: cross-turn fold | A new result folds against a prior result in the same session | < 10 ms |
| Tool result: no gain | Unique ~9.5 KB input returns unchanged | < 10 ms |
| CCR put | Unique payload per sample | < 5 ms |
| CCR get | Same stored payload read repeatedly (warm local-file path) | < 5 ms |

For stateful tool-result cases, session setup and prior-turn indexing happen outside the timed
interval. The p95 is reported to expose tail latency, but is not currently a pass/fail threshold.
CCR get intentionally measures repeated reads of one stored entry; it does not represent cold reads
across distinct files.

---

## Core capabilities

**Terminal reduction** (`transform_terminal_output`): ANSI purge, adjacent-repeat fold, Drain template fold (`×N` + verbatim sample), error rails (EN + Thai, verbatim), quality gate with `partial` overflow mode, per-command line budgets, never-inflate revert.

**Tool-result folding** (`transform_tool_result`): hygiene, volatile normalization (UUID/session/timestamp), lossless JSON minify (whole-doc or leading-prefix), cross-turn span fold, intra-document repeat fold, polarity-locked error preservation, shingle near-dedup (Jaccard ≥ 0.8), HEAD_TAIL stubs for large unique docs (≥100 KB), exemption list (ports/hashes/counts never fold), strict TOON for uniform arrays (opt-in).

**Retrieval safety rail (CCR)** (`ccr.py`): two-tier content-addressed retrieval cache (fast in-memory LRU + atomic local filesystem storage), every folded output stored by 12-char SHA-256 digest (`~/.hermes/plugin-data/trimtok/`), TTL + LRU eviction, sub-millisecond retrieval (0.1 ms median), exact roundtrip:
```json
{
  "name": "trimtok_retrieve",
  "arguments": {
    "hash": "57511e252ada",
    "offset": 1,
    "limit": 50,
    "grep": "error|fail"
  }
}
```
The original content is retrieved with line numbers or filtered without blowing up the context budget (patterns ≤256 chars, no groups, input ≤1 MB, output ≤1,000 matches / 100 KB).

**Usage telemetry** (`usage.jsonl` + `/trimtok usage`) — every hook invocation logged (all gates, fold shapes, retrieve hits), bounded 1 MB rotation, opt-out via `usage_enabled`.

---

## Configuration

Settings under `plugins.entries.trimtok.settings` in Hermes `config.yaml`:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | `bool` | `true` | Master switch for all hooks. Fail-open on `false`. |
| `phase2_enabled` | `bool` | `true` | Enables cross-turn tool result folding and path reductions. |
| `min_chars` | `int` | `2048` | Size threshold before compression (~512 tokens). |
| `exclude_commands` | `list` | `[]` | Command prefixes that bypass terminal compression. |
| `exclude_tools` | `list` | `[]` | Hermes tool names that bypass tool-result compression. |
| `max_lines` | `int` | `120` | Terminal line windowing limit. |
| `mask_enabled` | `bool` | `true` | Enables turn-clock expiry for fold index freshness. |
| `mask_keep_turns`| `int` | `2` | Recent turns retained in the fold index. |
| `audit_enabled`  | `bool` | `true` | Savings log (1 MB rotation). |
| `usage_enabled`  | `bool` | `true` | Usage log (1 MB rotation). |
| `ccr_max_entries`| `int` | `1000` | Cache entry cap (LRU). |
| `ccr_max_bytes`  | `int` | `52428800` | Cache byte budget (50 MB). |
| `ccr_ttl_hours`  | `int` | `24` | Original retention. |

Fold index bounded per session: 200 spans, 2 MiB source, 20,000 line keys.

---

## Slash commands

- `/trimtok status` — savings + cache entries (cleans expired).
- `/trimtok usage` — gate/shape breakdown, top-saving commands, retrieve hit rate.
- `/trimtok purge` — evict all cached originals.
- `/trimtok retrieve <hash>` — print a cached original.

---

## Installation

```bash
git clone https://github.com/Stxyu-p/trimtok.git "$LOCALAPPDATA/hermes/plugins/trimtok"
```

*(POSIX: `~/.hermes/plugins/trimtok`)*

```bash
hermes plugins enable trimtok
```

Verify:

```bash
python tests/test_core.py
python tests/test_dedup.py
python tests/test_benchmark.py
```

Hermes-runtime integration (isolated temp home, needs `HERMES_AGENT_PATH`):

```powershell
$env:HERMES_AGENT_PATH = "$env:LOCALAPPDATA\hermes\hermes-agent"
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" tests/test_hermes_runtime.py
```

---

## License

MIT License. Designed and maintained by P Choke & MIKA.
