<p align="center">
  <img src="assets/logo.png" width="160" alt="Trimtok Logo" />
</p>

# trimtok

**Universal, Local, Deterministic Context Compressor & Content Intake Shield for Autonomous AI Coding Agents**

[![PyPI](https://img.shields.io/pypi/v/trimtok?style=flat-square&color=blue)](https://pypi.org/project/trimtok/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Storage](https://img.shields.io/badge/storage-SQLite_WAL_+_Zstandard-07405E?style=flat-square)](#7-content-addressed-context-recovery-trimtok_retrieve)
[![Latency](https://img.shields.io/badge/latency-%3C_2ms_in--process-10b981?style=flat-square)](#-how-it-works)
[![Safety](https://img.shields.io/badge/safety-Fail--Open_Guarantee-8b5cf6?style=flat-square)](#9-fail-open-budget-guards)
[![CLI](https://img.shields.io/badge/cli-trimtok_wrap-EC4899?style=flat-square)](#standalone-cli-wrapper-trimtok-wrap)
[![Intake](https://img.shields.io/badge/intake-Tiered_Semantic_Shaving-059669?style=flat-square)](#4-tiered-web--file-intake-read_file--web_extract)
[![License: MIT](https://img.shields.io/badge/license-MIT-slate?style=flat-square)](LICENSE)

---

## ⚡ At a Glance: Before vs After

Autonomous coding agents burn over **60% of their context window budget** on repetitive, low-entropy noise. **trimtok** compresses this in-process without losing a single line of actionable truth:

```text
[pytest 200 tests]     56,000 chars  ──►     320 chars  (-99.4% cross-turn delta)
[FastAPI Traceback]    12,400 chars  ──►     890 chars  (-92.8% crash-site locked)
[Stripe API Array]      8,900 chars  ──►   3,100 chars  (-65.1% lossless TOON v2)
[Mixed-Schema JSON]    11,240 chars  ──►   6,630 chars  (-41.0% merged TOON table)
[Source Code 300 Fns]  13,500 toks   ──►   2,400 toks   (-82.2% multi-lang AST)
[Web Docs / Search]    15,200 chars  ──►   1,800 chars  (-88.1% tiered link shaving)
```

---

## 🏗️ How It Works

**trimtok** operates 100% locally and in-process using the Python standard library, RapidFuzz C++ alignment, tree-sitter AST parsing, and Zstandard compression. No external daemons, no Docker containers, and zero open network ports.

```text
Tool / Terminal Output
         │
         ▼
  ┌────────────────────────────────────────────────────────┐
  │  trimtok In-Process Hook (< 2 ms, fail-open ≤ 200 ms)  │
  │  ├── 1. Cross-Turn Delta Engine (pytest, cargo test)   │
  │  ├── 2. Command-Specific Filters (git, npm, pip, etc.) │
  │  ├── 3. Structured Traceback Pruning (locks crash-site)│
  │  ├── 4. Lossless & Merged TOON v2 (JSON arrays)        │
  │  ├── 5. Multi-Language AST Skeletonizer (7 languages)  │
  │  └── 6. Tiered Web & Doc Link De-bloating              │
  └──────────────────────────┬─────────────────────────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   Agent Context Window             SQLite WAL + Zstandard
   (Clean, dense token stream)     (Full recovery via `#c1`)
```

---

## 📑 Table of Contents

- [Market Comparison (Head-to-Head)](#-market-comparison-trimtok-vs-alternatives)
- [Core Engineering Capabilities](#-core-engineering-capabilities)
  - [Pillar 1: Terminal & Command Compression](#pillar-1-terminal--command-compression)
  - [Pillar 2: Content & Code Intake Shield](#pillar-2-content--code-intake-shield)
  - [Pillar 3: Zero Data Loss & Self-Healing Telemetry](#pillar-3-zero-data-loss--self-healing-telemetry)
- [First-Time Setup](#-first-time-setup-5-minutes)
- [Native Hermes Agent Plugin Configuration](#-native-hermes-agent-plugin-configuration)
- [Slash Commands & Standalone CLI](#-slash-commands--standalone-cli)
- [Part of the Global Agent Trinity](#-part-of-the-global-agent-trinity)
- [License](#-license)

---

## 🥊 Market Comparison: trimtok vs Alternatives

Empirical head-to-head benchmarks measured on identical production workloads:

| Capability / Benchmark | **trimtok v5.3.0** 🌟 | **RTK v1.1.0** (Rust) | **headroom v0.42.0** (Python) | **skim v2.10.0** (Rust) | **sqz v2.1.1** (Rust) | **tokenfold** (Python/Rust) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Execution Layer** | **In-process hook + CLI** | Subprocess CLI | Python library / Proxy | Subprocess CLI | Subprocess CLI | Python FFI library |
| **Uniform JSON (500 rows)** | **55.3% saved** | 99.6% (truncated) | **61.7% saved** | N/A | ~55% saved | 55.7% saved |
| **Mixed JSON (450 rows)** | **41.0% – 44.7%** | 99.6% (truncated) | **46.3% saved** | N/A | 42.6% – 45.2% | 24.0% saved |
| **Code AST (300 functions)** | **82.2% (AST stubs)** | 0% (or wipes 100%) | N/A | **82.2% (signatures)** | N/A | N/A |
| **Cross-Turn Delta Mode** | ✅ **`[tt:delta]` (>95%)** | ❌ None | ❌ None | ❌ None | ❌ None | ❌ None |
| **Framework Traceback Prune**| ✅ **77.0% (locks crash)** | ❌ Stripped or raw | ❌ None | ❌ None | ❌ None | ❌ None |
| **Command-Specific Filters** | ✅ **6 commands** | ✅ 12 commands | ❌ None | ❌ None | ❌ None | ❌ None |
| **Full Original Retrieval** | ✅ **Content-addressed (`#c1`)** | ❌ Lost permanently | ⚠️ CCR via MCP | ❌ Lost permanently | ⚠️ §ref dedup | ✅ Exact bytes |
| **In-Process Latency** | **< 2 ms** (micro) | 10 – 50 ms (process) | ~9,054 ms (73x slower)| ~259 ms (process) | ~437 ms (process) | 660 – 860 ms |
| **Zero Network Ports** | ✅ **100% portless** | ✅ Portless | ❌ Opens HTTP port | ✅ Portless | ❌ Opens HTTP/Web | ✅ Portless |

### Where trimtok Excels
- **In-Process & Sub-2ms:** Direct hook invocation with zero subprocess overhead (headroom takes ~9s, sqz takes ~450ms).
- **Cross-Turn Stateful Delta:** Re-running test suites (`pytest`, `cargo test`) only prints active errors and diffs vs prior turn, slashing >95% tokens.
- **Zero Data Loss Guarantee:** Everything transformed is stored into local SQLite WAL + Zstandard. The agent recalls original bytes at any time via `#c1`.
- **Intake Shielding:** Automatically prunes link bloat and boilerplate headers on `web_extract` and `read_file` without offset drift.

### Where trimtok is Weaker
- **Language Coverage on AST:** trimtok supports 7 languages (Python, JS/TS, Go, Rust, Java, C, C++). `skim` supports 17 languages via native tree-sitter.
- **Command Filter Coverage:** trimtok has 6 targeted command filters (`git log`, `git status`, `ls`, `npm`, `pip`, `find`). `RTK` provides 12 command-specific heuristics.
- **Extreme Mixed-JSON Compression:** `headroom` achieves 46.3% on heterogeneous JSON via smart_crusher transforms, while trimtok achieves 41.0% – 44.7% with merged TOON.

> [!NOTE]
> **Production Telemetry Baseline (pre-5.3.0):** 4,456 events, 11.66M characters input → 1.10M characters saved (9.5% overall aggregate across all raw traffic; 27.2% terminal hook; 5.2% tool results). v5.3.0 capabilities (merged TOON, 6 command filters, multi-language AST) are newly active.

---

## ⚡ Core Engineering Capabilities

### Pillar 1: Terminal & Command Compression

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

#### 2. Command-Specific Output Filters
High-volume CLI tools are filtered to essential summaries backed by CCR retrieval:
- **`git log`** → commit count, author digest, date range.
- **`git status`** → current branch, staged/unstaged/untracked counters.
- **`ls -la`** → file/folder count breakdown by extension.
- **`npm install / list`** → package installation summary and dependency counts.
- **`pip install / list`** → installed package names and satisfied requirements count.
- **`find`** → matched file counts grouped by extension.

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

### Pillar 2: Content & Code Intake Shield

#### 4. Tiered Web & File Intake (`read_file` & `web_extract`)
- **Inline URL De-bloat:** Converts verbose links `[Anchor Text](https://very-long-url/path "Title")` into clean `Anchor Text`, cutting **35% – 45% of markdown link bloat in 2–4 ms**.
- **Heading + 2-Line Factsheet Digest:** Keeps headings, tables, code blocks, and the first 2 descriptive lines per section, folding excessive narrative into line-anchored CCR `#ref` stubs (**76% – 96% token reduction**).
- **Patch-Safe License Stripping:** Automatically removes open-source license headers and divider comments on files < 14KB with **Zero Offset Drift**.

#### 5. Multi-Language AST Code Skeletonizer
For code files ≥ 2KB, trimtok parses the structural AST using tree-sitter:
- **Supported Languages:** Python, JavaScript/TypeScript, Go, Rust, Java, C, C++.
- **Zero Loss of Structure:** Retains all class definitions, function signatures, interfaces, imports, and docstrings.
- **Folds Oversized Bodies:** Function implementations ≥ 8 lines fold into clean CCR-referenced stubs, preserving line structure for patch safety.

#### 6. Lossless & Merged-Schema TOON v2
Converts nested JSON arrays into columnar tables validated by in-memory equality roundtrips:
- **Uniform Arrays:** 55% – 57% token reduction.
- **Mixed Schemas (Merged TOON):** Automatically unifies heterogeneous object structures into a single table with null-filled gaps, achieving 41% – 44.7% savings without splitting into disjoint buckets.
```text
[tt:legend] TOON table (lossless): @prefix(~/)=https://example.com/items/
@data.results[3]{id,title,url,tags}:
0|Item 0|~/0|["dev","lead"]
1|Item 1|~/1|["qa"]
2|Item 2|~/2|["pm","ops"]
```

---

### Pillar 3: Zero Data Loss & Self-Healing Telemetry

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
```

#### 8. Autonomous Telemetry & Diagnostics (`gain` & `discover`)
trimtok self-monitors every transformation in `~/.trimtok/usage.jsonl`:
- **`trimtok gain`**: Breaks down real savings by output shape, tool hook, and command family, highlighting compression bottlenecks.
- **`trimtok discover`**: Scans production telemetry for high-frequency, low-savings patterns and recommends targeted filters.

#### 9. Fail-Open Budget Guards
- **Strict 200 ms Monotonic Deadline (`guard_ms: 200.0`):** If compression exceeds the deadline or RapidFuzz alignment is slow, trimtok aborts instantly and emits raw output.
- **Zero Agent Freezes:** No timeout cascades, no hangs, no broken builds.

---

## 🛠️ First-Time Setup (5 Minutes)

### Step 1: Install Trimtok
```bash
pip install trimtok
```

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

### Step 3: Verify Registration & Storage Health
```bash
trimtok clients
trimtok status
```

---

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
        delta_enabled: true         # Enable Cross-Turn Command Delta engine
        delta_threshold: 0.65       # Similarity ratio threshold for delta mode (0.50 - 1.00)
        mask_enabled: true          # Enable stale turn span expiration
        mask_keep_turns: 2          # Retain full unmasked history for N active turns
        excluded_commands: []       # Exact command stems to bypass compression
        excluded_tools: []          # Tool names to leave 100% untouched
```

---

## 💻 Slash Commands & Standalone CLI

### Hermes In-Chat Slash Commands
- **`/trimtok status`**: Displays active CCR database size, cache entries, Zstandard compression metrics, and engine health.
- **`/trimtok usage`**: Shows live telemetry breakdown across terminal and tool hooks (events, folds, chars saved, top gates).
- **`/trimtok gain`**: Detailed savings breakdown by shape, tool, and command — identifies bottlenecks.
- **`/trimtok discover`**: Scans telemetry → finds low-savings shapes/tools → suggests targeted filters.
- **`/trimtok purge`**: Purges expired cache entries and vacuums the SQLite WAL database.
- **`/trimtok recall <#ref>`**: Retrieves original output by CCR reference (alias for `retrieve`).

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
```

---

## 🌐 Part of the Global Agent Trinity

**trimtok** forms the **Global Agent Trinity** alongside **MemCore** and **Skill Proof** — unifying context, governed memory, and local skills across all your AI coding agents without daemons or open network ports:

| Pillar | Hub | Role |
| :--- | :--- | :--- |
| **[trimtok](https://github.com/Stxyu-p/trimtok)** | `~/.trimtok` | **Context Shield:** Cuts terminal, traceback, web, and JSON bloat in-process with instant `#c1` CCR recovery. |
| **[MemCore](https://github.com/Stxyu-p/memcore)** | `~/.memcore` | **Governed Memory:** Shared bitemporal memory with hard SQL scope boundaries, 1-bit vector quantization (32x smaller RAM), and Tainted Context Guard. |
| **[Skill Proof](https://github.com/Stxyu-p/skill-proof)** | `~/.skill-proof` | **Skill Vault & Router:** Sub-2ms deterministic router, multi-lingual negation veto, and OS NTFS read-only Immutable SOP protection. |

```bash
# Install and link all three pillars in one pass
pip install trimtok memcore-omnix skill-proof-router
trimtok install && memcore install && skill-proof install
```

---

## 📄 License

Distributed under the [MIT License](LICENSE). Designed and crafted for robust, high-leverage autonomous software engineering.
