<p align="center">
  <img src="assets/logo.png" width="160" alt="Trimtok Logo" />
</p>

# trimtok

**Universal, Local, Deterministic Context Compressor & Content Intake Shield for Autonomous AI Coding Agents**

[![PyPI](https://img.shields.io/pypi/v/trimtok?style=flat-square&color=blue)](https://pypi.org/project/trimtok/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Storage](https://img.shields.io/badge/storage-SQLite_WAL_+_Zstandard-07405E?style=flat-square)](#content-addressed-context-recovery-ccr)
[![Latency](https://img.shields.io/badge/latency-%3C_2ms_in--process-10b981?style=flat-square)](#the-solution-in-process-deterministic-compression-in-12-ms)
[![Safety](https://img.shields.io/badge/safety-Fail--Open_Guarantee-8b5cf6?style=flat-square)](#core-engineering-guarantees)
[![CLI](https://img.shields.io/badge/cli-trimtok_wrap-EC4899?style=flat-square)](#standalone-cli-wrapper-trimtok-wrap)
[![Intake](https://img.shields.io/badge/intake-Tiered_Semantic_Shaving-059669?style=flat-square)](#tiered-web--file-intake-read_file--web_extract)
[![License: MIT](https://img.shields.io/badge/license-MIT-slate?style=flat-square)](LICENSE)

---

## 🚀 Overview

Autonomous coding agents (Claude Code, Cursor, Windsurf, Codex, Antigravity, Hermes) burn over **60% of their context window budget** on repetitive, low-entropy noise:
- Massive test suites (`pytest`, `cargo test`, `npm test`) repeating hundreds of identical passing dots.
- Deep framework stack traces (15+ internal frames in `starlette`, `fastapi`, `node_modules`).
- Web scrapes and search results bloated with redundant URL link targets, cookie banners, and navigation lists.
- Nested JSON payloads filled with repetitive dictionary keys.

### The False Dilemma in the Market
- **Naive Truncation (`head -n 50` or tail cuts):** Destroys critical compiler diagnostics, test failure summaries, and exception crash sites hidden in the middle of output, causing the agent to hallucinate fixes for the wrong problem.
- **Secondary LLM Summarization:** Adds 2–5 seconds of network latency per tool call, burns extra tokens, incurs recursive API costs, and frequently hallucinates altered line numbers or error codes.
- **Aggressive Signature Stripping (RTK Trap):** Completely wipes out function bodies down to signatures (`-l aggressive`), leaving the agent blind to implementation logic and forcing repetitive multi-turn re-reads (`read_file` offset loops).

### Capability Matrix (Function-by-Function)

| Capability | trimtok |
| :--- | :---: |
| **Cross-turn delta** | ✅ `[tt:delta]` |
| **Traceback pruning** | ✅ 77% |
| **TOON JSON encoding** | ✅ 57% |
| **Code skeleton (multi-lang)** | ✅ 7 langs |
| **Web/doc intake** | ✅ 77-96% |
| **Full original retrieval** | ✅ `#c1` CCR |
| **In-process latency** | < 2 ms |
| **Fail-open guarantee** | ✅ |
| **Command-specific filters** | ✅ 6 cmds |
| **Telemetry/gain command** | ✅ `gain` |
| **Discover (low-savers)** | ✅ `discover` |

**Honest production telemetry (pre-5.3.0 baseline):** 4,456 events, 11.66M chars → 1.10M saved (9.5% overall, 27.2% terminal, 5.2% tool). Numbers from real agent sessions on v5.2.0 and earlier. v5.3.0 features are newly shipped — no production data yet.

---
**trimtok** operates 100% locally and in-process using Python standard library, RapidFuzz C++ token alignment, and Zstandard compression. No external daemons, no Docker containers, and zero open network ports.

---

## 🥊 Market Comparison: trimtok vs Alternatives

| Capability | trimtok | RTK | Repomix | Jina/Trafilatura | Squeez |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Cross-turn delta | ✅ `[tt:delta]` | ❌ | ❌ | ❌ | ❌ |
| Traceback pruning | ✅ 77% | ❌ | ❌ | ❌ | ❌ |
| TOON JSON encoding | ✅ 57% | ❌ | ❌ | ❌ | ❌ |
| Code skeleton | ✅ 7 langs | ⚠️ sigs only | ⚠️ wipes bodies | ❌ | ⚠️ cosmetic |
| Web/doc intake | ✅ 77-96% | ❌ | ❌ | ⚠️ 40% bloat left | ❌ |
| Full original retrieval | ✅ `#c1` CCR | ❌ lost | ❌ | ❌ | ❌ |
| In-process latency | < 2 ms | 10-50 ms | 50-200 ms | 500-2500 ms | < 1 ms |

**Where trimtok is stronger:** Cross-turn delta, traceback pruning, web/doc intake, lossless retrieval, in-process speed.

**Where trimtok is weaker:** Code skeleton covers 7 languages (skim covers 17). Command-specific filters cover 6 commands (RTK covers 12). Mixed-schema JSON savings 41% (headroom 46%).

**Honest production telemetry (pre-5.3.0 baseline):** 4,456 events, 11.66M chars → 1.10M saved (9.5% overall, 27.2% terminal, 5.2% tool). Numbers from real agent sessions on v5.2.0 and earlier. v5.3.0 features (filters, skeletonizer, merged TOON) are newly shipped — no production data yet.

---

## ⚡ Core Engineering Capabilities

### 1. Cross-Turn Command Delta Engine (`compress_terminal`)
When an agent re-runs `pytest`, `cargo test`, or build commands after editing code:
- **Raw Terminal Output:** 56,000+ characters of passing test output.
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

### 2. Tiered Web & File Intake (`read_file` & `web_extract`)
- **Inline URL De-bloat:** Converts verbose markdown links `[Anchor Text](https://very-long-url/path "Title")` into clean `Anchor Text`, cutting **35% – 45% of link bloat in 2–4 ms** without losing a single semantic concept.
- **Heading + 2-Line Factsheet Digest:** Keeps all headings, tables, and code blocks, plus the leading 2 descriptive lines per section, folding excessive narrative paragraphs into line-anchored CCR `#ref` stubs (**76% – 96% token reduction on real Wikipedia, MDN, and Python docs**).
- **Patch-Safe Small Code Cleaning (< 14KB):** Automatically removes bulky open-source license headers and ASCII divider banners with **100% Patch Line Safety (Zero Offset Drift)**.
- **Single-Turn Comprehension (> 20KB):** Unlike RTK which strips all code, Trimtok preserves all short utility functions (< 20 lines) in full, folding only oversized methods into stubs.

### 3. Structured Traceback Pruning
Internal framework frames (FastAPI, Starlette, Node modules) are collapsed into clear summary indicators while strictly locking the entrypoint, user project code, and exact exception crash site (**77% reduction**):
```text
Traceback (most recent call last):
  File "/workspace/app/api/endpoints.py", line 45, in handle_payment
    result = await service.charge_card(req)
  [... 10 framework frames omitted ...]
  File "/workspace/app/services/billing.py", line 88, in charge_card
    return gateway.execute_transaction(card_id, amount)
ZeroDivisionError: division by zero
```

### 4. Lossless TOON v2 JSON Transcoding
Converts deeply nested JSON arrays into hierarchical columnar tables, validated by a strict in-memory equality roundtrip (**57% reduction**):
```text
[tt:legend] TOON table (lossless): @prefix(~/)=https://example.com/items/
@data.results[3]{id,title,url,tags}:
0|Item 0|~/0|["dev","lead"]
1|Item 1|~/1|["qa"]
2|Item 2|~/2|["pm","ops"]
```

### 5. Content-Addressed Context Recovery (`trimtok_retrieve`)
Every transformed payload is preserved in a local SQLite WAL + Zstandard database (`~/.trimtok/trimtok_ccr_v2.sqlite3`). Agents can recall any original byte using short `#c1` references:
```bash
# Retrieve original or targeted slices directly from shell
trimtok retrieve "#c1"
trimtok retrieve "#c1" --section errors      # Error lines with surrounding context
trimtok retrieve "#c1" --section diff        # Unified diff hunks with headers
trimtok retrieve "#c1" --section tail        # Last N lines
trimtok retrieve "#c1" --grep "ConnectionReset" # Bounded regex search
### 6. Command-Specific Output Filters
Noisy dev commands are detected and replaced with compact summaries + CCR reference:
- **`git log`** → commit count, authors, date range
- **`git status`** → branch, staged/unstaged/untracked counts
- **`ls -la`** → file count by type (files/dirs)
- **`npm install`** → package count + installed names
- **`pip install`** → installed package count
- **`find`** → file count by extension
- Full output preserved via `#c1` CCR reference

### 7. Multi-Language AST Code Skeletonizer
Source files ≥ 2 KB are skeletonized using tree-sitter AST parsing:
- **Languages:** Python, JavaScript/TypeScript, Go, Rust, Java, C, C++
- **Preserves:** function/class signatures, braces, imports
- **Folds:** function bodies ≥ 8 lines
- **Patch-safe:** original file untouched; CCR reference for full content

### 8. Merged-Schema TOON
Heterogeneous JSON arrays (mixed schemas) are unified into a single TOON table with null-filled missing fields — better compression than bucketing by schema alone.

---

## 🛠️ First-Time Setup (5 Minutes)

### Step 1: Install Trimtok
```bash
pip install trimtok
```

### Step 2: Run Unified Client Setup Wizard
Automatically configures the local SQLite WAL store, verifies Zstandard acceleration, and registers the stdio MCP server across all detected AI coding environments on your machine:
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

To install Trimtok as a native plugin in Hermes Agent:
```bash
# Windows
git clone https://github.com/Stxyu-p/trimtok.git "$env:LOCALAPPDATA\hermes\plugins	rimtok"

# Linux / macOS
git clone https://github.com/Stxyu-p/trimtok.git ~/.hermes/plugins/trimtok

# Enable plugin in Hermes
hermes plugins enable trimtok
```

### Configuration Options (`~/.hermes/config.yaml` or `plugin.yaml`)
Customize Trimtok's engine behavior:
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
| **[trimtok](https://github.com/Stxyu-p/trimtok)** | `~/.trimtok` | **Context Shield:** Cuts terminal, traceback, web, and JSON bloat by 38–48% (real telemetry) with instant `#c1` CCR recovery. |
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
