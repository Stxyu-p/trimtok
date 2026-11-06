<h1 align="center">⚡ trimtok</h1>

<p align="center">
  <strong>Universal, Local, Deterministic Context Compressor &amp; Content Intake Shield for Autonomous AI Coding Agents</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/trimtok/"><img src="https://img.shields.io/badge/pypi-v5.5.0-blue?style=flat-square" alt="PyPI" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square" alt="Python" /></a>
  <a href="#7-ccr-recovery"><img src="https://img.shields.io/badge/storage-SQLite_WAL_+_Zstandard-07405E?style=flat-square" alt="Storage" /></a>
  <a href="#how-it-works"><img src="https://img.shields.io/badge/latency-%3C_2ms_in--process-10b981?style=flat-square" alt="Latency" /></a>
  <a href="#9-fail-open-guards"><img src="https://img.shields.io/badge/safety-Fail--Open_Guarantee-8b5cf6?style=flat-square" alt="Safety" /></a>
  <a href="#10-content-policy"><img src="https://img.shields.io/badge/policy-Secrets_Stay_Verbatim-dc2626?style=flat-square" alt="Policy" /></a>
  <a href="#11-adaptive-level"><img src="https://img.shields.io/badge/level-Adaptive_Compression-f59e0b?style=flat-square" alt="Adaptive" /></a>
  <a href="#12-model-aware"><img src="https://img.shields.io/badge/output-Model--Aware_TOON-6366f1?style=flat-square" alt="Model-Aware" /></a>
  <a href="#standalone-cli-wrapper"><img src="https://img.shields.io/badge/cli-trimtok_wrap-EC4899?style=flat-square" alt="CLI" /></a>
  <a href="#4-tiered-web-intake"><img src="https://img.shields.io/badge/intake-Tiered_Semantic_Shaving-059669?style=flat-square" alt="Intake" /></a>
  <a href="#14-fix-accuracy"><img src="https://img.shields.io/badge/verified-8%2F8_fix_locate-0ea5e9?style=flat-square" alt="Fix Accuracy" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-slate?style=flat-square" alt="License" /></a>
</p>

<p align="center">
  <img src="assets/before-after.svg" width="100%" alt="trimtok - Before vs After Context Shield" />
</p>

---

<a id="at-a-glance"></a>
## ⚡ At a Glance: Before vs After

Autonomous coding agents burn over **60% of their context window budget** on repetitive, low-entropy noise. **trimtok** compresses this in-process without losing a single line of actionable truth:

```text
[pytest 200 tests]     56,000 chars  ──►     320 chars  (-99.4% cross-turn delta)
[FastAPI Traceback]    12,400 chars  ──►     890 chars  (-92.8% crash-site locked)
[Stripe API Array]      8,900 chars  ──►   3,100 chars  (-65.1% lossless TOON v2)
[Mixed-Schema JSON]    11,240 chars  ──►   6,630 chars  (-41.0% merged TOON table)
[Source Code 300 Fns]  13,500 toks   ──►   2,400 toks   (-82.2% multi-lang AST)
[Web Docs / Search]    15,200 chars  ──►   1,800 chars  (-88.1% tiered link shaving)
[JSON File 50 Cols]    12,002 toks   ──►   6,225 toks   (-48.1% file TOON)
[OpenAPI Document]      2,746 toks   ──►   1,367 toks   (-50.2% JSON minify)
[Build Log 400 Lines]  15,960 toks   ──►   4,869 toks   (-69.5% adaptive budget)
[MCP Schema 25 Tools]   5,800 toks   ──►     780 toks   (-86.5% bounded proxy)
[Repo Exploration 4k]  12,000 toks   ──►     480 toks   (-96.0% 2k-budget map)
[22k-token source]     21,999 toks   ──►     571 toks   (-97.4% intent retrieval)
```

Measured across a 32-payload production corpus (1,135,441 characters): **73.2% reduction with zero gate failures**, of which every removed character is verifiable through `trimtok_retrieve`. Retrieval is billed only when the agent asks a question, taking the same corpus from 74.8% to **89.1%** saved.

---

<a id="how-it-works"></a>
## 🏗️ How It Works

**trimtok** operates 100% locally and in-process using the Python standard library, RapidFuzz C++ alignment, tree-sitter AST parsing, and Zstandard compression. No external daemons, no Docker containers, and zero open network ports.

```text
  ┌────────────────────────────────────────────────────────┐
  │ Tier 0: Pre-Flight Context Shield (Zero Disk Footprint)│
  │ ├── Bounded MCP Proxy: filters tools/list schemas      │
  │ └── Codebase Symbol Map: 2k-budget symbol directory map│
  └──────────────────────────┬─────────────────────────────┘
                             ▼
Tool / Terminal Output
         │
         ▼
  ┌────────────────────────────────────────────────────────┐
  │ Tier 1: In-Process Hot-Path (< 2 ms, fail-open ≤ 200ms)│
  │ ├── 0. Content Policy Gate (secrets / edits verbatim)  │
  │ ├── 1. Cross-Turn Delta Engine (pytest, cargo test)    │
  │ ├── 2. Command-Specific Filters (git, npm, pip, etc.)  │
  │ ├── 3. Structured Traceback Pruning (locks crash-site) │
  │ ├── 4. Lossless & Merged TOON v2 (JSON arrays)         │
  │ ├── 5. Multi-Language AST Skeletonizer (7 languages)   │
  │ └── 6. Tiered Web & Doc Link De-bloating               │
  └──────────────────────────┬─────────────────────────────┘
                             │  level: light │ normal │ aggressive
                             │  format: TOON │ minified JSON
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   Agent Context Window             SQLite WAL + Zstandard
   (Clean, dense token stream)     (Tier 2 Recovery via `#c1`)
```

---

## 📑 Table of Contents

<table width="100%">
<tr>
<td width="33%" valign="top">

#### 🏛️ Architecture & Benchmarks
- [⚡ At a Glance: Before vs After](#at-a-glance)
- [🏗️ How It Works](#how-it-works)
- [🥊 Architecture & Benchmark Matrix](#market-comparison)
  - [Where trimtok Excels](#where-trimtok-excels)
  - [Where trimtok is Weaker](#where-trimtok-is-weaker)

</td>
<td width="34%" valign="top">

#### ⚡ 4 Engineering Pillars
- **[Pillar 1: Terminal Compression](#pillar-1-terminal)**
  - [Cross-Turn Delta Engine](#1-cross-turn-delta)
  - [Command-Specific Filters](#2-command-filters)
  - [Structured Traceback Pruning](#3-traceback-pruning)
- **[Pillar 2: Intake Shield](#pillar-2-intake)**
  - [Tiered Web & File Intake](#4-tiered-web-intake)
  - [AST Code Skeletonizer](#5-ast-skeletonizer)
  - [Lossless TOON v2 (JSON)](#6-toon-v2)
- **[Pillar 3: Zero Data Loss](#pillar-3-zero-data-loss)**
  - [CCR Recovery (`#ref`)](#7-ccr-recovery)
  - [Telemetry & Diagnostics](#8-telemetry-diagnostics)
  - [Fail-Open Budget Guards](#9-fail-open-guards)
- **[Pillar 4: Policy, Adaptivity & Verification](#pillar-4-policy)**
  - [Content Policy Gate](#10-content-policy)
  - [Adaptive Compression Level](#11-adaptive-level)
  - [Model-Aware Output Format](#12-model-aware)
  - [Retention Controls](#13-retention)
  - [Fix-Accuracy Verification](#14-fix-accuracy)

</td>
<td width="33%" valign="top">

#### 🛠️ Setup & Operations
- **[First-Time Setup (5 Mins)](#first-time-setup)**
  - [Step 1: Install Trimtok](#step-1-install)
  - [Step 2: Setup Wizard](#step-2-wizard)
  - [Step 3: Health Verification](#step-3-health)
- **[Hermes Plugin Setup](#hermes-configuration)**
  - [Configuration Options](#hermes-config-options)
- **[Slash Commands & CLI](#slash-commands-cli)**
  - [In-Chat Slash Commands](#hermes-slash-commands)
  - [KV-Cache Stabilizer (`kv-lock`)](#kvlock-cli)
  - [Codebase Map (`map`)](#codemap-cli)
  - [Bounded MCP Proxy (`mcp-proxy`)](#mcp-proxy-cli)
  - [Standalone Wrapper (`wrap`)](#standalone-cli-wrapper)
  - [Command Rewriting (`rewrite`)](#rewrite-cli)
- **[Global Agent Trinity](#global-agent-trinity)**
- **[Verification](#verification)**
- **[License (MIT)](#license)**

</td>
</tr>
</table>

---

<a id="market-comparison"></a>
## 🥊 Architecture & Benchmark Matrix: trimtok vs Industry Engines

Empirical head-to-head benchmarks measured on identical production workloads:

| Capability / Benchmark | **trimtok v5.5.0** 🌟 | **claw-compactor v7.0** (OpenClaw) | **RTK v1.1.0** (Rust) | **headroom v0.42.0** (Python) | **skim v2.10.0** (Rust) | **sqz v2.1.1** (Rust) | **tokenfold** (Python/Rust) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Execution Layer** | **In-process hook + CLI** | 14-Stage Pipeline + Node Proxy | Subprocess CLI | Python library / Proxy | Subprocess CLI | Subprocess CLI | Python FFI library |
| **Uniform JSON (500 rows)** | **55.3% saved** (TOON v2) | **81.9% (lossy sampling)** | 99.6% (truncated) | **61.7% saved** | N/A | ~55% saved | 55.7% saved |
| **Mixed JSON (450 rows)** | **41.0% – 44.7%** (merged TOON) | ~40% – 50% | 99.6% (truncated) | **46.3% saved** | N/A | 42.6% – 45.2% | 24.0% saved |
| **Code AST (300 functions)** | **82.2% (AST stubs)** | 18.4% – 25.0% (whitespace/comments) | 0% (or wipes 100%) | N/A | **82.2% (signatures)** | N/A | N/A |
| **Cross-Turn Delta Mode** | ✅ **`[tt:delta]` (>95%)** | ❌ None (per-message log filter) | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None |
| **Framework Traceback Prune**| ✅ **77.0% (locks crash-site)** | ✅ LogCrunch (stack/error scan) | ❌ Stripped or raw | ❌ None | ❌ None | ❌ None | ❌ None |
| **Command-Specific Filters** | ✅ **7 commands** (`git`, `npm`, `pip`, etc.) | ⚠️ 3 crunchers (Log/Diff/Search) | ✅ 12 commands | ❌ None | ❌ None | ❌ None | ❌ None |
| **Full Original Retrieval** | ✅ **Content-addressed (`#c1`)** | ⚠️ `RewindStore` (24-char hex) | ❌ Lost permanently | ⚠️ CCR via MCP | ❌ Lost permanently | ⚠️ §ref dedup | ✅ Exact bytes |
| **Recovery Storage Backend** | ✅ **SQLite WAL + Zstd (Persistent on-disk)** | ⚠️ In-memory RAM (`OrderedDict` LRU) | ❌ None (Lossy) | ⚠️ Local disk cache | ❌ None (Lossy) | ⚠️ In-stream reference map | ⚠️ In-memory RAM |
| **Full-Text Payload Search** | ✅ **SQLite FTS5 BM25 (`<5ms`)** | ⚠️ In-memory substring scan | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None |
| **Task-Aware Code Map** | ✅ **`trimtok map --focus`** | ❌ None | ❌ None | ❌ None | ⚠️ Full AST (unbudgeted) | ❌ None | ❌ None |
| **Decision-Equivalence Eval** | ✅ **100% (0% divergence)** | ⚠️ SWE-bench partial | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None |
| **Intent-Addressed Retrieval** | ✅ **`--intent` (93–97%)** | ⚠️ Keyword search (`store.search`) | ❌ None | ❌ None (full block only) | ❌ None | ❌ None | ❌ None |
| **Content Policy Gate** | ✅ **Secrets & edits verbatim** | ⚠️ Annotations only (`noqa`, `TODO`) | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None |
| **Adaptive Level** | ✅ **Pressure-scaled (light/normal/aggressive)** | ⚠️ Stage gating (`should_apply`) | ❌ Static CLI flags | ❌ Fixed heuristics | ❌ Static CLI depth | ❌ Static ratio target | ❌ Static threshold |
| **Model-Aware Format** | ✅ **`auto` / `always` / `never`** | ❌ None (generic token opt) | ❌ Single format | ❌ Single format | ❌ Single format | ❌ Single format | ❌ Single format |
| **KV-Cache / Prompt Alignment** | ✅ **`trimtok kv-lock` (<date>, code-safe)** | ✅ `QuantumLock` (prefix-stable) | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None |
| **Bounded MCP Tool Proxy** | ✅ **`trimtok mcp-proxy`** | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None |
| **Codebase Symbol Map** | ✅ **`trimtok map` (<38ms, budgeted)** | ❌ None | ❌ None | ❌ None | ⚠️ Full AST (unbudgeted) | ❌ None | ❌ None |
| **Fix-Accuracy Benchmark** | ✅ **8/8 locate** (`eval/fix_accuracy.py`) | ✅ SWE-bench instance tests | ❌ Unit tests only | ❌ Synthetic only | ❌ Unit tests only | ❌ Unit tests only | ❌ Token counts only |
| **In-Process Latency** | **< 2 ms** (micro) | ~50 – 88 ms | 10 – 50 ms (process) | ~9,054 ms (73x slower)| ~259 ms (process) | ~437 ms (process) | 660 – 860 ms |
| **Zero Network Ports** | ✅ **100% portless** | ❌ Opens HTTP port (Node proxy) | ✅ Portless | ❌ Opens HTTP port | ✅ Portless | ❌ Opens HTTP/Web | ✅ Portless |

<a id="where-trimtok-excels"></a>
### Where trimtok Excels
- **In-Process & Sub-2ms:** Direct hook invocation with zero subprocess overhead (headroom takes ~9s, sqz takes ~450ms, claw-compactor takes ~50ms).
- **Cross-Turn Stateful Delta:** Re-running test suites (`pytest`, `cargo test`) only prints active errors and diffs vs prior turn, slashing >95% tokens.
- **Persistent Zero Data Loss Guarantee:** Everything transformed is stored into local SQLite WAL + Zstandard (`FastCCRStore`). Unlike in-memory caches that vanish on process exit or across subagents, trimtok originals remain durable and recallable via `#c1`.
- **Intake Shielding:** Automatically prunes link bloat and boilerplate headers on `web_extract` and `read_file` without offset drift.
- **3-Tier Lifecycle Protection:** Guards System Prompts (KV-Cache alignment & Bounded MCP schema pruning), Exploratory Turns (Codebase Map), and Hot-Path Payloads simultaneously.

<a id="where-trimtok-is-weaker"></a>
### Where trimtok is Weaker
- **Language Coverage on AST:** trimtok supports 7 languages (Python, JS/TS, Go, Rust, Java, C, C++). `skim` supports 17 languages and `claw-compactor` supports 16 languages via native tree-sitter.
- **Command Filter Coverage:** trimtok has 7 targeted command filters (`git log`, `git status`, `ls`, `npm`, `pip`, `find`, `docker ps`). `RTK` provides 12 command-specific heuristics.
- **Extreme Mixed-JSON Compression:** `headroom` achieves 46.3% on heterogeneous JSON via smart_crusher transforms, while trimtok achieves 41.0% – 44.7% with merged TOON.
- **NLP Perplexity Benchmarks:** `claw-compactor` publishes empirical ROUGE-L scores (0.653 @ 0.3) across SWE-bench tasks; trimtok prioritizes deterministic structural fidelity and exact code recoverability over lossy perplexity pruning.
- **Model-Awareness is a Heuristic:** `toon_mode: auto` keys off the model *family name*, not a measured per-model capability. TOON is plain text and every model can read it; the setting only decides which format is likely to survive a long context best.
- **Fix-Accuracy Needs a Live Model:** the `LOCATE` stage runs offline and deterministically, but the `FIX` stage that proves a patch actually works requires a reachable model endpoint.

> [!NOTE]
> **Production Telemetry Baseline (pre-5.3.0):** 4,456 events, 11.66M characters input → 1.10M characters saved (9.5% overall aggregate across all raw traffic; 27.2% terminal hook; 5.2% tool results). Capabilities added since v5.3.0 — merged TOON, 7 command filters, multi-language AST, JSON file intake, content policy, adaptive levels — are active in current releases but not reflected in this historical baseline.

---

<a id="core-capabilities"></a>
## ⚡ Core Engineering Capabilities

<a id="pillar-1-terminal"></a>
### Pillar 1: Terminal & Command Compression

<a id="1-cross-turn-delta"></a>
#### 1. Cross-Turn Command Delta Engine (`compress_terminal`)
When an agent edits code and re-runs test suites or build commands:
- **Raw Output:** 56,000+ characters of unchanged passing dots.
- **trimtok Output (~300 characters — >95% reduction):**
```text
[tt:delta cmd="pytest" sim=99% +#1 -#1 !#1 #c2 #c1]
[tt:legend] Delta vs previous run: + added, - resolved, ! still failing; refs = full original via trimtok_retrieve.
--- STILL FAILING / ACTIVE ERRORS (1) ---
! FAILED tests/test_auth.py::test_login - AssertionError: expected 200 got 401
--- ADDED / CHANGED IN THIS TURN (+1) ---
+ ===== 1 failed, 200 passed in 43.2s =====
--- RESOLVED / REMOVED (-1) ---
- ===== 1 failed, 200 passed in 42.1s =====
```

<a id="2-command-filters"></a>
#### 2. Command-Specific Output Filters
High-volume CLI tools are filtered to essential summaries backed by CCR retrieval:
- **`git log`** → commit count, author digest, date range.
- **`git status`** → current branch, staged/unstaged/untracked counters.
- **`ls -la`** → file/folder count breakdown by extension.
- **`npm install / list`** → package installation summary and dependency counts.
- **`pip install / list`** → installed package names and satisfied requirements count.
- **`find`** → matched file counts grouped by extension.
- **`docker ps / container ls`** → running container count, image breakdown, and service names.

<a id="3-traceback-pruning"></a>
#### 3. Structured Traceback Pruning
Internal framework frames (FastAPI, Starlette, Node modules) collapse into compact summaries while strictly locking the entrypoint, user project code, and exact exception crash site (**77% reduction**):
```text
Traceback (most recent call last):
  File "/workspace/app/api/endpoints.py", line 45, in handle_payment
    result = await service.charge_card(req)
  [... 10 framework frames omitted ...]
  File "/workspace/app/services/billing.py", line 88, in charge_card
    return gateway.execute_transaction(card_id, amount)
ZeroDivisionError: division by zero
```

---

<a id="pillar-2-intake"></a>
### Pillar 2: Content & Code Intake Shield

<a id="4-tiered-web-intake"></a>
#### 4. Tiered Web & File Intake (`read_file` & `web_extract`)
- **Inline URL De-bloat:** Converts verbose links `[Anchor Text](https://very-long-url/path "Title")` into clean `Anchor Text`, cutting **35% – 45% of markdown link bloat in 2–4 ms**.
- **Heading + 2-Line Factsheet Digest:** Keeps headings, tables, code blocks, and the first 2 descriptive lines per section, folding excessive narrative into line-anchored CCR `#ref` stubs (**76% – 96% token reduction**).
- **Patch-Safe License Stripping:** Automatically removes open-source license headers and divider comments on files < 14KB with **Zero Offset Drift**.

<a id="5-ast-skeletonizer"></a>
#### 5. Multi-Language AST Code Skeletonizer
For code files ≥ 2KB, trimtok parses the structural AST using tree-sitter:
- **Supported Languages:** Python, JavaScript/TypeScript, Go, Rust, Java, C, C++.
- **Zero Loss of Structure:** Retains all class definitions, function signatures, interfaces, imports, and docstrings.
- **Folds Oversized Bodies:** Function implementations ≥ 8 lines fold into clean CCR-referenced stubs, preserving line structure for patch safety.

<a id="6-toon-v2"></a>
#### 6. Lossless & Merged-Schema TOON v2
Converts nested JSON arrays into columnar tables validated by in-memory equality roundtrips:
- **Uniform Arrays:** 55% – 57% token reduction.
- **Mixed Schemas (Merged TOON):** Automatically unifies heterogeneous object structures into a single table with null-filled gaps, achieving 41% – 44.7% savings without splitting into disjoint buckets.
- **Wide Tables:** Up to 128 columns columnarize losslessly (50 columns × 20 rows: **48.1%**).
- **Deep Cells:** Nested objects longer than the inline cap are stored behind an explicit `=` marker instead of rejecting the whole table, so GitHub- and Stripe-shaped payloads compress too (**28.8%** on a 60-row repository listing).
- **JSON Files:** `read_file` on a `.json` path routes through the same codec, or whole-document minification for non-uniform objects (OpenAPI document: **50.2%**). Both paths are round-trip verified and leave a resolvable `#ref`.
```text
[tt:legend] TOON table (lossless): @prefix(~/)=https://example.com/items/
@data.results[3]{id,title,url,tags}:
0|Item 0|~/0|["dev","lead"]
1|Item 1|~/1|["qa"]
2|Item 2|~/2|["pm","ops"]
```

---

<a id="pillar-3-zero-data-loss"></a>
### Pillar 3: Zero Data Loss & Self-Healing Telemetry

<a id="7-ccr-recovery"></a>
#### 7. Content-Addressed Context Recovery (`trimtok_retrieve`)
Every transformed payload is preserved in a local SQLite WAL + Zstandard database (`~/.trimtok/trimtok_ccr_v2.sqlite3`). Agents recall original bytes anytime:
```bash
# Retrieve original or targeted slices directly from shell
trimtok retrieve "#c1"
trimtok retrieve "#c1" --section errors      # Error lines with surrounding context
trimtok retrieve "#c1" --section diff        # Unified diff hunks with headers
trimtok retrieve "#c1" --section tail        # Last N lines
trimtok retrieve "#c1" --grep "ConnectionReset" # Bounded regex search
trimtok retrieve "#c1" --offset 50 --limit 30   # Line-numbered pagination

# Answer a question instead of paging through the file
trimtok retrieve "#c1" --intent "which functions parse JSON" --intent-budget 80
```

Long-horizon workflows can pin critical CCR references to protect them from automated TTL expiration sweeps:
```python
import trimtok.ccr as ccr
ccr.pin("#c1")    # Protects chunk from background TTL cleanup
ccr.unpin("#c1")  # Restores default TTL lifecycle
```

`--intent` ranks lines by stemmed token overlap with the question and boosts definition and heading lines, so a signature arrives with its body. On a 21,999-token source file, *"which functions does this file define"* returns **1,458 tokens (93.4% saved)** and *"how is json minified"* returns **571 tokens (97.4% saved)**, each containing the answer line rather than a truncated guess.

<a id="8-telemetry-diagnostics"></a>
#### 8. Autonomous Telemetry & Diagnostics (`gain` & `discover`)
trimtok self-monitors every transformation in `~/.trimtok/usage.jsonl`:
- **`trimtok gain`**: Breaks down real savings by output shape, tool hook, and command family, highlighting compression bottlenecks.
- **`trimtok discover`**: Scans production telemetry for high-frequency, low-savings patterns and recommends targeted filters.
- **Distributed Trace Correlation (`TRINITY_TRACE_ID`)**: Automatically captures `TRINITY_TRACE_ID` from the environment and attaches `trace_id` to each telemetry record, enabling unified correlation across Context, Memory, and Skill governance layers.

<a id="9-fail-open-guards"></a>
#### 9. Fail-Open Budget Guards
- **Strict 200 ms Monotonic Deadline (`guard_ms: 200.0`):** If compression exceeds the deadline or RapidFuzz alignment is slow, trimtok aborts instantly and emits raw output.
- **Zero Agent Freezes:** No timeout cascades, no hangs, no broken builds.

---

<a id="pillar-4-policy"></a>
### Pillar 4: Policy, Adaptivity & Verification

<a id="10-content-policy"></a>
#### 10. Content Policy Gate
Not every payload should be compressed. Before any transformation runs, trimtok classifies the input and keeps byte-exact what would lose its meaning if a single character moved. These return `gate=passthrough_policy` with a named reason, never a silently mangled payload:

| Class | Matched by | Reason |
| :--- | :--- | :--- |
| **Credentials** | `.env`, `.npmrc`, `.netrc`, `*.pem`, `*.key`, `id_rsa`, `credentials.*` | `secret_path` |
| **Inline secrets** | AWS `AKIA…`, GitHub `ghp_…`, OpenAI `sk-…`, Slack `xox…`, JWT `eyJ…`, PEM blocks, `password = …` | `secret_value` |
| **Security policy data** | `security/`, `policies/`, `auth/`, `rbac/` containing YAML, JSON, TOML, INI, Rego or Terraform | `security_path` |
| **Edit instructions** | `patch`, `edit_file`, `write_file`, `apply_patch`, or any unified diff | `edit_tool` / `patch_payload` |

Source code under `security/` or `auth/` still compresses, because the AST skeletonizer keeps every signature and leaves a resolvable `#ref` — a 2,000-line `auth.py` remains navigable rather than being excluded wholesale. Detection is deliberately narrow: a false positive costs one uncompressed payload, while a false negative would rewrite a credential in place and make it impossible to copy verbatim.

<a id="11-adaptive-level"></a>
#### 11. Adaptive Compression Level
Compression strength follows the remaining context budget instead of applying one fixed policy to every turn. `context_pressure` (0.0 – 1.0, supplied per call by the host hook or set in config) selects a level that scales the terminal line budget and the AST body-fold threshold:

| Pressure | Level | Line budget | Body fold threshold |
| :--- | :--- | :---: | :---: |
| < 0.35 | `light` | ×1.6 | 20 lines |
| 0.35 – 0.75 | `normal` | ×1.0 | 8 lines |
| > 0.75 | `aggressive` | ×0.55 | 4 lines |

Measured on a 400-line unique build log (15,960 tokens): **light 51.5%** (195 lines kept), **normal 69.5%** (123 lines), **aggressive 83.1%** (69 lines). Set `adaptive_enabled: false` to pin the level at `normal`.

<a id="12-model-aware"></a>
#### 12. Model-Aware Output Format
TOON is plain text, so every model can read it; what differs is how well a model holds a columnar table together over a long context. `toon_mode` decides which format the agent receives:

| Mode | Behaviour |
| :--- | :--- |
| `auto` *(default)* | TOON for model families with a track record on columnar text; minified JSON otherwise |
| `always` | TOON whenever it wins on tokens |
| `never` | Always minified JSON |

`model_family` supplies the target (e.g. `claude`, `gpt`, `qwen`). When it is unset, `auto` keeps TOON enabled so no existing deployment silently changes format. Both formats are lossless: the same 30 × 20 JSON payload produced 3,881 characters as TOON versus 8,401 as minified JSON, with identical round-trip equality.

<a id="13-retention"></a>
#### 13. Retention Controls
Recovery storage is bounded explicitly rather than growing until someone notices:

```bash
trimtok purge --older-than 24     # Remove entries older than 24 hours
trimtok purge --max-mb 50         # Evict oldest entries until the store is under 50 MB
trimtok purge --ttl-hours 72      # Persist a 72-hour retention window
```

`--max-mb` alone evicts by age without wiping the store first, and the TTL is stored in the database `meta` table so it survives restarts. Defaults: 1,000 entries, 50 MB, 24 hours.

<a id="14-fix-accuracy"></a>
#### 14. Fix-Accuracy Verification
Token counts say nothing about whether an agent can still do the job. `eval/fix_accuracy.py` injects a known regression into a real module, compresses it, and asks whether the bug is still fixable:

- **`LOCATE`** *(default, no model required)* — verifies the buggy line, its enclosing signature, its line number and neighbour context are either visible in the compressed view or reachable through `#ref`. A payload that hides the bug without a reference fails, because the agent would have to re-read the file.
- **`FIX`** *(`--llm`)* — sends the compressed view to a model, takes back the corrected line, patches the real module, imports it and runs a behavioural predicate. A pass means the repaired code works, not that the text looks plausible.

Current result: **8/8 locate accuracy** across Python, TOON codec, dedup and sentinel modules, at **53.8% token reduction**. The suite includes a negative control (`test_wrong_patch_fails`) so a benchmark that cannot fail is itself caught.

---

<a id="first-time-setup"></a>
## 🛠️ First-Time Setup (5 Minutes)

<a id="step-1-install"></a>
### Step 1: Install Trimtok
```bash
pip install trimtok
```

<a id="step-2-wizard"></a>
### Step 2: Run Unified Client Setup Wizard
Automatically configures the local SQLite WAL store, verifies Zstandard acceleration, and registers the stdio MCP server across all detected AI coding environments:
```bash
trimtok install
```
Auto-detects and registers:
- **Claude Code** (`~/.claude.json`) & **Claude Desktop**
- **Cursor** (`~/.cursor/mcp.json`), **Windsurf**, and **VS Code (Copilot)**
- **Google Antigravity & Antigravity IDE** (`mcp_config.json`)
- **Gemini CLI** (`settings.json`) & **Codex CLI** (`config.toml`)
- **OpenCode** & **Cline**

<a id="step-3-health"></a>
### Step 3: Verify Registration & Storage Health
```bash
trimtok clients
trimtok status
```

---

<a id="hermes-configuration"></a>
## ⚙️ Native Hermes Agent Plugin Configuration

To install trimtok as a native plugin in Hermes Agent:

```bash
# Windows (PowerShell)
git clone https://github.com/Stxyu-p/trimtok.git "$env:LOCALAPPDATA/hermes/plugins/trimtok"

# Linux / macOS (Bash)
git clone https://github.com/Stxyu-p/trimtok.git ~/.hermes/plugins/trimtok

# Enable plugin in Hermes
hermes plugins enable trimtok
```

<a id="hermes-config-options"></a>
### Configuration Options (`~/.hermes/config.yaml` or `plugin.yaml`)
```yaml
plugins:
  entries:
    trimtok:
      settings:
        enabled: true               # Master toggle for Trimtok compression
        phase2_enabled: true        # Enable tool-result compression (TOON, AST, Intake)
        guard_ms: 200.0             # Monotonic deadline budget (fail-open if exceeded)
        min_chars: 2048             # Minimum character threshold before compression fires
        toon_enabled: true          # Enable Lossless TOON v2 JSON transcoding
        toon_mode: auto             # Model-aware format: auto | always | never
        model_family: ""            # Target model family (claude, gpt, qwen); empty keeps TOON on
        policy_enabled: true        # Keep secrets, security data and edit payloads byte-exact
        adaptive_enabled: true      # Scale compression strength with context pressure
        context_pressure: 0.0       # Fraction of context window used (0.0 - 1.0)
        delta_enabled: true         # Enable Cross-Turn Command Delta engine
        delta_threshold: 0.65       # Similarity ratio threshold for delta mode (0.50 - 1.00)
        mask_enabled: true          # Enable stale turn span expiration
        mask_keep_turns: 2          # Retain full unmasked history for N active turns
        ccr_ttl_hours: 24           # Retention window for the recovery store
        ccr_max_bytes: 52428800     # Hard ceiling for the recovery store (50 MB)
        ccr_max_entries: 1000       # Maximum cached payloads
        excluded_commands: []       # Exact command stems to bypass compression
        excluded_tools: []          # Tool names to leave 100% untouched
```

---

<a id="slash-commands-cli"></a>
## 💻 Slash Commands & Standalone CLI

<a id="hermes-slash-commands"></a>
### Hermes In-Chat Slash Commands
- **`/trimtok status`**: Displays active CCR database size, cache entries, Zstandard compression metrics, and engine health.
- **`/trimtok usage`**: Shows live telemetry breakdown across terminal and tool hooks (events, folds, chars saved, top gates).
- **`/trimtok gain`**: Detailed savings breakdown by shape, tool, and command — identifies bottlenecks.
- **`/trimtok discover`**: Scans telemetry → finds low-savings shapes/tools → suggests targeted filters.
- **`/trimtok purge`**: Purges expired cache entries and vacuums the SQLite WAL database. Accepts `--older-than <hours>`, `--max-mb <size>` and `--ttl-hours <hours>`.
- **`/trimtok recall <#ref>`**: Retrieves original output by CCR reference (alias for `retrieve`).

<a id="standalone-cli-wrapper"></a>
### Standalone CLI Wrapper (`trimtok wrap`)
Run noisy developer commands directly in your terminal or CI/CD pipelines without Hermes:
```bash
# Wrap test suites with automatic terminal compression and CCR storage
trimtok wrap -- pytest tests/
trimtok wrap -- cargo test

# Strictly preserves child process exit code
trimtok wrap -- npm test || echo "Failed with exit code $?"

# Retrieve original output anytime
trimtok retrieve "#c1"
trimtok recall "#c1" --offset 50 --limit 30

# Sub-millisecond SQLite FTS5 search across all captured payloads
trimtok search "ConnectionRefused"
trimtok search "AssertionError in auth" --limit 3
```

<a id="kvlock-cli"></a>
### KV-Cache Prompt Stabilizer (`trimtok kv-lock`)
Lock system prompt prefixes to maximize prompt-cache hit rates (up to 90% cost reduction on Anthropic, Gemini, and DeepSeek) by replacing dynamic dates, times, and UUIDs outside code blocks with stable placeholders, relocating their original values to a delimited appendix at the tail:
```bash
# Stabilize a system prompt file or stdin
trimtok kv-lock prompt.txt

# Pipe through shell
cat prompt.txt | trimtok kv-lock
```
*Output preview:*
```markdown
You are an expert software engineer.
System started on <date> at <time>.
Session ID: <uuid>

---
<!-- kv-lock: dynamic context -->
date: 2026-11-05
time: 14:30:00
uuid: 550e8400-e29b-41d4-a716-446655440000
<!-- end kv-lock -->
```

<a id="codemap-cli"></a>
### Task-Aware Codebase Map (`trimtok map`)
Scan repository structure and output top-level symbols grouped by directory within a strict character budget (default 2,000 characters, executed in < 38 ms). Supports `--focus` to dynamically prioritize target file imports and reverse references at the top of the map:
```bash
# Generate compact symbol map of current directory
trimtok map .

# Task-aware focus: elevate service.py and its dependencies to the top
trimtok map . --focus src/service.py --budget 1200
```
*Output preview:*
```text
Codebase map (4 directories):
trimtok/: detect_kind, compress_agent_payload, main, default_dir, build_codebase_map, filter_mcp_message, handle
eval/: declared_hidden, add, tok, verify_patch_safety, refs_in, run_test
trimtok/strategies/: minify_lossless, strip_signatures_rtk, shave_tiered
```

<a id="mcp-proxy-cli"></a>
### Bounded MCP Stdio Proxy (`trimtok mcp-proxy`)
Intercept stdio JSON-RPC communication between any coding harness and third-party MCP servers to prune bloated tool schemas from the model's system prompt:
```bash
# Allow only essential tools from a sprawling server
trimtok mcp-proxy --allow "codegraph_explore" -- npx -y @colbymchenry/codegraph

# Deny diagnostic/admin tools that waste system prompt tokens
trimtok mcp-proxy --deny "ctx_doctor,ctx_stats,ctx_purge" -- npx -y context-mode
```

*Drop-in configuration for `mcp.json` / `opencode.json`:*
```json
{
  "mcpServers": {
    "context-mode": {
      "command": "python",
      "args": [
        "-m", "trimtok", "mcp-proxy",
        "--deny", "ctx_doctor,ctx_stats,ctx_purge",
        "--", "npx", "-y", "context-mode"
      ]
    }
  }
}
```

<a id="rewrite-cli"></a>
### Command Rewriting (`trimtok rewrite`)
Prints a noise-reduced form of a command without executing it, for use in shell aliases or CI scripts:
```bash
trimtok rewrite -- pytest tests/    # -> pytest tests/ -q --tb=short
trimtok rewrite -- git log          # -> git log --oneline -n 25
```
Explicit flags are respected: `trimtok rewrite -- pytest -v` returns `pytest -v` unchanged. `trimtok wrap` applies the same rewrite before execution.

---

<a id="verification"></a>
## 🧪 Verification

Every number in this README is reproducible from the repository:

```bash
python eval/eval_real.py        # Per-payload reduction + gate failures on the 32-payload corpus
python eval/audit_honesty.py    # Claimed vs proven reduction; unproven characters must be 0
python eval/steps.py            # raw -> hook -> intent retrieval, per payload
python eval/fix_accuracy.py     # Can an agent still fix the bug? (LOCATE, no model needed)
python eval/decision_eval.py    # Decision-equivalence quality contract (0% decision divergence)
python eval/probe_ceiling.py    # Are folded lines announced and ref-reachable?
pytest tests/                   # 119 unit tests
```

The honesty audit is the load-bearing check: it recomputes the claimed reduction from the recovery store and reports any character that was removed without a resolvable reference. Run `eval_real.py` immediately before `audit_honesty.py` against a fresh store — pointing both at the same empty `TRIMTOK_DATA_DIR` — so the audit scores exactly the corpus the first script produced. Current state — **73.19% claimed, 73.19% proven, 0 unproven characters**, 0 gate failures, 0 silent line loss, 0 unannounced faults, 119/119 tests passing.

---

<a id="global-agent-trinity"></a>
## 🌐 Part of the Global Agent Trinity

**trimtok** forms the **Global Agent Trinity** alongside **MemCore** and **Skill Proof** — unifying context, governed memory, and local skills across all your AI coding agents without daemons or open network ports:

| Pillar | Hub | Role |
| :--- | :--- | :--- |
| **[trimtok](https://github.com/Stxyu-p/trimtok)** | `~/.trimtok` | **Context Shield:** Cuts terminal, traceback, web, and JSON bloat in-process with instant `#c1` CCR recovery. |
| **[MemCore](https://github.com/Stxyu-p/memcore)** | `~/.memcore` | **Governed Memory:** Shared bitemporal memory with hard SQL scope boundaries, deterministic lexical recall, and Tainted Context Guard. |
| **[Skill Proof](https://github.com/Stxyu-p/skill-proof)** | `~/.skill-proof` | **Skill Vault & Router:** Sub-2ms deterministic router, multi-lingual negation veto, and OS NTFS read-only Immutable SOP protection. |

```bash
# Install and link all three pillars in one pass
pip install trimtok memcore-omnix skill-proof-router
trimtok install && memcore install && skill-proof install
```

---

<a id="license"></a>
## 📄 License

Distributed under the [MIT License](LICENSE). Designed and crafted for robust, high-leverage autonomous software engineering.
