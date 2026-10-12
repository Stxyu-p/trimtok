<h1 align="center">trimtok</h1>

<p align="center">
  <strong>Local stdlib-only context compressor and token optimizer for Hermes Agent.</strong>
</p>

<p align="center">
  <a href="#benchmarks"><img src="docs/badges/latency.svg" alt="Latency" /></a>
  <a href="#architecture"><img src="docs/badges/dependencies.svg" alt="Zero Dependencies" /></a>
  <a href="#safety-rail-ccr"><img src="docs/badges/safety.svg" alt="Fail-open" /></a>
  <a href="#installation"><img src="docs/badges/ecosystem.svg" alt="Hermes Agent" /></a>
  <a href="LICENSE"><img src="docs/badges/license.svg" alt="License" /></a>
</p>

---

## Overview

Large language model agents consume significant token bandwidth processing voluminous command line logs, repeated file outputs, and redundant tool responses.

`trimtok` is a lightweight, zero-dependency plugin for Hermes Agent designed to reduce context token consumption. Compression may omit or replace parts of an output; the exact original can be retrieved from CCR only while its entry remains available (subject to TTL and cache eviction). The local, deterministic pipeline uses only the Python standard library.

```
   Raw Output (Terminal / Tool)
               │
               ▼
   ┌─────────────────────────────────────────────────────────┐
   │  trimtok Stage 1: Fast Heuristics & Terminal Reduction   │
   │  • ANSI Stripping       • Error Rail Sentinel Guard     │
   │  • Loop Dedup Collapse  • Head/Tail Adaptive Window     │
   ├─────────────────────────────────────────────────────────┤
   │  trimtok Stage 2: Cross-Turn Output Deduplication       │
   │  • Normalized Index     • Span Folding [tt:... ptr]     │
   │  • Path Reductions      • Safe JSON Minification        │
   ├─────────────────────────────────────────────────────────┤
   │  Content-Addressed Retrieval (CCR) Safety Rail          │
   │  • Hash: sha256[:12]    • Cache: ~/.hermes/.../ccr/     │
   │  • Fast O(1) Local Disk • Recovery: trimtok_retrieve    │
   └─────────────────────────────────────────────────────────┘
               │
               ▼
   Clean, Compact Context to Model
```

<p align="center">
  <img src="docs/architecture-dark.svg" alt="trimtok Architecture" width="100%">
</p>

---

## Benchmarks

The benchmark suite uses synthetic inputs as a local regression signal, not as a production latency
guarantee. Run it with python tests/test_benchmark.py. Each path has five warm-ups and 50 timed
samples. The suite reports median and nearest-rank p95; the regression gate applies to the median.

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

## Core Capabilities

### 1. Terminal Stream Reduction (`transform_terminal_output`)
- **ANSI Escape Code Purge**: Strips terminal colors and cursor codes in a single pass.
- **Repeat Collapse**: Collapses repetitive status bars, build progress bars, and polling lines (`48 duplicate lines collapsed`).
- **Error Rail Protection**: Preserves lines matching stack traces, exceptions, panics, assertion errors, and failure indicators verbatim.
- **Adaptive Head & Tail Windowing**: Maintains the initial invocation state and the terminal conclusion while collapsing uninformative interior loops.

### 2. Cross-Turn Deduplication (`transform_tool_result`)
- **Normalized Fingerprinting**: Indexes large outputs across agent conversational turns.
- **Path-Prefix Collapse**: Compresses verbose repetitive file directory paths in search results.
- **Span Folding**: Substitutes redundant multi-line payloads seen in previous turns with compact pointer markers `[tt:tool kind=result saved=N retrieve:<hash>]`.
- **JSON Minifier**: Strips non-semantic whitespace on structured responses without schema distortion.

### 3. Content-Addressed Retrieval (CCR) Safety Rail
Every compressed or folded output is stored by its 12-character SHA-256 digest in the local CCR cache (`~/.hermes/plugin-data/trimtok/ccr/`).
If an agent requires the exact uncompressed payload, it calls the registered tool with optional line pagination or regex filtering:
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
The original content is retrieved and restored with line numbers or filtered without blowing up the context budget. Regex filtering uses a bounded subset: patterns are limited to 256 characters, groups and multiple repetition operators are rejected, input is limited to 1 MB, and output to 1,000 matches / 100 KB.

---

## Configuration

Settings are declared under `plugins.entries.trimtok.settings` in Hermes `config.yaml`:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | `bool` | `true` | Master switch for all hooks. Fail-open on `false`. |
| `phase2_enabled` | `bool` | `true` | Enables cross-turn tool result folding and path reductions. |
| `min_chars` | `int` | `2048` | Threshold size in characters before compression triggers. |
| `exclude_commands` | `list` | `[]` | Normalized, case-insensitive command prefixes that bypass terminal compression. Example: `git status` also excludes `git status --short`. |
| `exclude_tools` | `list` | `[]` | Exact, case-insensitive Hermes tool names that bypass tool-result compression. |
| `max_lines` | `int` | `120` | Terminal line windowing limit. |
| `mask_enabled` | `bool` | `true` | Enables turn-clock expiry for fold index freshness. |
| `mask_keep_turns`| `int` | `2` | Number of recent turns retained in the active fold index. |
| `audit_enabled`  | `bool` | `true` | Record compression savings to a rotating audit log (1 MB plus one backup). |
| `ccr_max_entries`| `int` | `1000` | Maximum cached original entries before LRU eviction. |
| `ccr_max_bytes`  | `int` | `52428800` | Total cache byte budget (default: 50 MB). |
| `ccr_ttl_hours`  | `int` | `24` | Time-to-live for cached originals. |

The in-memory fold index is bounded per session to 200 spans, 2 Mi characters of retained source
text, and 20,000 unique long-line keys. An individual result larger than the text budget is still
returned normally, but is not retained as a fold candidate. Exact-repeat hashes remain for the
session lifetime and are cleared by the session-end hook.

---

## Slash Commands

In interactive Hermes sessions:

- `/trimtok status` or `/trimtok stats` — Display recent character savings and active cache entry count. Expired CCR files are cleaned during status checks.
- `/trimtok purge` — Evict all cached CCR files.
- `/trimtok retrieve <hash>` — Print cached original text for a given hash.

---

## Installation

### 1. Deploy Plugin Directory
Clone or copy the repository into your Hermes plugins directory:

```bash
git clone https://github.com/Stxyu-p/trimtok.git "$LOCALAPPDATA/hermes/plugins/trimtok"
```
*(On POSIX systems, target `~/.hermes/plugins/trimtok`)*

### 2. Enable in Hermes
Enable the plugin through the Hermes CLI:
```bash
hermes plugins enable trimtok
```

### 3. Run Tests
Verify test suites and microsecond benchmarks locally:
```bash
python tests/test_core.py
python tests/test_dedup.py
python tests/test_benchmark.py
```

An optional integration test loads the plugin through Hermes' runtime in an isolated temporary home, then checks compression and exact retrieval. It skips when `HERMES_AGENT_PATH` is not set. `TRIMTOK_PLUGIN_PATH` can point it at a different plugin copy; otherwise it tests this checkout. Run it with the Python environment used by Hermes:

```powershell
$env:HERMES_AGENT_PATH = "$env:LOCALAPPDATA\hermes\hermes-agent"
$env:TRIMTOK_PLUGIN_PATH = "$env:LOCALAPPDATA\hermes\plugins\trimtok" # optional: test installed copy
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" tests/test_hermes_runtime.py
```

---

## License

MIT License. Designed and maintained by P Choke & MIKA.
