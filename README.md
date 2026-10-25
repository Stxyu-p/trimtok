# trimtok

**Universal, Local, Deterministic Context Compressor for Autonomous AI Coding Agents**

Real-world production telemetry across thousands of agent turns proves **48.5% character reduction on terminal outputs** and **38.7% on tool results** — with zero intelligence lost and every original byte instantly recoverable via Content-Addressed Context Recovery (`#c1`).

[![PyPI](https://img.shields.io/pypi/v/trimtok?style=flat-square&color=blue)](https://pypi.org/project/trimtok/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Storage](https://img.shields.io/badge/storage-SQLite_WAL_+_Zstandard-07405E?style=flat-square)](#content-addressed-context-recovery-ccr)
[![Latency](https://img.shields.io/badge/latency-%3C_2ms_in--process-10b981?style=flat-square)](#the-solution-deterministic-context-compression-in-12-ms)
[![Safety](https://img.shields.io/badge/safety-Fail--Open_Guarantee-8b5cf6?style=flat-square)](#core-engineering-guarantees)
[![License: MIT](https://img.shields.io/badge/license-MIT-slate?style=flat-square)](LICENSE)

---

## The Problem: Context Window Bloat & The Truncation Trap

Autonomous coding agents (Claude Code, Cursor, Antigravity, Codex, Windsurf, Hermes) burn over 60% of their context budget on repetitive, low-entropy noise:
- Thousands of lines of passing test suite output (`pytest`, `cargo test`, `npm test`).
- Deep framework stack traces (20+ internal frames in `starlette`, `fastapi`, or `node_modules`).
- Hundreds of unchanged `git diff` context lines during minor code edits.
- Repetitive `grep` / search match boilerplate.
- Deeply nested JSON tool payloads filled with redundant metadata keys.

### The False Dilemma
- **Naive Truncation (`head -n 50` or tail cuts):** Destroys critical compiler diagnostics, test failure summaries, or exception crash sites hidden in the middle of output, causing the agent to hallucinate fixes for the wrong problem.
- **Secondary LLM Summarization:** Adds 2–5 seconds of network latency per tool call, burns extra tokens, incurs recursive API costs, and risks hallucinating altered line numbers or error codes.

---

## The Solution: In-Process Deterministic Compression in ~1–2 ms

**trimtok** solves this 100% locally and in-process using Python standard library, RapidFuzz C++ token alignment, and Zstandard compression. No external daemons, no Docker containers, and zero open network ports.

1. **Terminal Pipeline (`compress_terminal`):** Strips ANSI/spinner escape artifacts, prunes internal framework stack frames while keeping entrypoints and crash sites, condenses unchanged `git diff` context, folds identical and Drain-template log repeats, and turns repeated test/build runs into a compact **Cross-Turn Stateful Delta**.
2. **Tool & Code Pipeline (`compress_tool_result`):** Deduplicates repeated content across turns, groups multi-file search hits under enclosing function/class scopes, condenses large source files via **AST Code Skeletonization** (preserving imports, signatures, types, and docstrings while folding bodies), and transcodes nested JSON into lossless **Hierarchical TOON Tables** verified by an in-memory strict equality roundtrip.
3. **Content-Addressed Context Recovery (`CCR`):** Every compressed payload is stored in a local SQLite WAL + Zstandard database (`~/.trimtok/trimtok_ccr_v2.sqlite3`). The agent receives a short Base62 reference (e.g., `[tt:... #c1]`) and can retrieve exact sections (`errors`, `diff`, `summary`, `tail`), run safe regex grep, or restore the uncompressed original on demand.

---

## Core Engineering Guarantees

| Guarantee | How It Works |
| :--- | :--- |
| **Zero Intelligence Loss** | Every error line, exception, crash site, compiler diagnostic, HTTP 5xx code, and failure indicator (`is_important`) is locked by fault polarity rails and is **never** dropped or folded away. |
| **BPE Non-Inflation Gate** | Every candidate output is checked against a BPE token estimator. If compression fails to strictly reduce both character count and token count, trimtok returns the untouched original string. |
| **Fail-Open Deadline Guard** | Monotonic deadline timers (`guard_ms`, default 50 ms) protect every transformation. Any timeout or unexpected exception immediately returns the raw original text. |
| **Multi-Process Subagent Safety** | Sequence allocation (`#c1`, `#c2`) is executed atomically inside SQLite (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`). Parallel subagents never generate colliding aliases. |
| **Patch-Safe File Reads** | Direct file reads under targeted line limits or cold reads remain 100% verbatim, ensuring code editing tools (`patch`, `edit_file`) always retain valid line anchors. |

---

## One-Command Setup Wizard

Install trimtok and run the Setup Wizard to configure the global SQLite WAL store, verify Zstandard acceleration, and automatically register the stdio MCP server across all detected AI coding clients on your machine:

```bash
pip install trimtok
trimtok install
```

`trimtok install` detects and automatically registers:
- **Claude Code** (`~/.claude.json`) & **Claude Desktop**
- **Google Antigravity & Antigravity IDE** (`mcp_config.json`)
- **Gemini CLI** (`settings.json`) & **Codex CLI** (`config.toml`)
- **Cursor** (`~/.cursor/mcp.json`), **Windsurf**, **VS Code (Copilot)**, **OpenCode**, and **Cline**

Verify client registration and storage health anytime:

```bash
trimtok clients
trimtok status
```

---

## Before & After Showcase

### 1. Cross-Turn Command Delta (Repeated Test Run)

When an agent re-runs `pytest` or `cargo test` after editing code:

**Raw Terminal Output (56,000+ characters of passing logs):**
```text
============================= test session starts =============================
platform win32 -- Python 3.12, pytest-8.3.2, pluggy-1.5.0
collected 201 items
tests/test_auth.py ............................                         [ 13%]
tests/test_db.py ...................................................... [ 40%]
... [1,950 lines of passing test output & noisy INFO logs] ...
================================== FAILURES ===================================
FAILED tests/test_auth.py::test_login - AssertionError: expected 200 got 401
======================== 1 failed, 200 passed in 43.2s ========================
```

**trimtok Compressed Output (~300 characters — 99% reduction):**
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

---

### 2. Structured Traceback Pruning

Framework library noise in stack traces is collapsed into clear summary indicators while keeping the entrypoint, user project code, and exact crash line intact:

```text
Traceback (most recent call last):
  File "/workspace/app/main.py", line 25, in handle_request
    result = pipeline.process(data)
  [... 14 framework frames in site-packages/starlette & fastapi omitted ...]
  File "/workspace/app/pipeline.py", line 58, in process
    return 100 / count
ZeroDivisionError: division by zero
```

---

### 3. Lossless TOON JSON Transcoding

Verbose nested JSON arrays and API responses are converted to hierarchical columnar tables with factored path prefixes, verified by strict in-memory roundtrip validation before emission:

```text
[tt:legend] TOON table (lossless): @prefix(~/)=https://example.com/items/
@data.results[3]{id,title,url,tags}:
0|Item 0|~/0|["dev","lead"]
1|Item 1|~/1|["qa"]
2|Item 2|~/2|["pm","ops"]
```

---

## Content-Addressed Context Recovery (`trimtok_retrieve`)

Whenever an output is compressed, its unmodified original is preserved in `~/.trimtok/trimtok_ccr_v2.sqlite3`. Agents can call `trimtok_retrieve` via MCP or CLI using the `#c1` reference:

```bash
# Retrieve original or targeted sections directly from shell
trimtok retrieve "#c1"
trimtok retrieve "#c1" --section errors      # Error lines with surrounding context
trimtok retrieve "#c1" --section diff        # Unified diff hunks with headers
trimtok retrieve "#c1" --section tail        # Last N lines
trimtok retrieve "#c1" --grep "ConnectionReset" # Case-insensitive bounded regex search
trimtok retrieve "#c1" --offset 50 --limit 30   # 1-indexed line pagination
```

### MCP Tools Exposed

- **`trimtok_compress_terminal`**: Compresses noisy shell command output while locking 100% of error diagnostics and stack traces.
- **`trimtok_compress_tool`**: Compresses tool results (JSON, code, grep searches) via TOON tables, AST skeletonization, and span deduplication.
- **`trimtok_retrieve`**: Retrieves uncompressed original content or targeted slices (`errors`, `diff`, `summary`, `tail`, `grep`, pagination) by `#c1` reference.
- **`trimtok_status`**: Returns CCR storage metrics, cache entries, and compression telemetry.

---

## Standalone CLI & Pipe Usage

```bash
# Compress terminal logs from shell pipe
cat test.log | trimtok compress --command "pytest"

# Compress large tool output files
trimtok compress --file api_response.json --tool "fetch_data"

# Inspect system status and CCR cache metrics
trimtok status

# View telemetry breakdown across commands and tools
trimtok usage

# Purge expired cache entries and vacuum database
trimtok purge
```

---

## Native Hermes Agent Plugin

To install trimtok as a native Hermes Agent plugin:

```bash
# Linux / macOS
git clone https://github.com/Stxyu-p/trimtok.git ~/.hermes/plugins/trimtok

# Windows
git clone https://github.com/Stxyu-p/trimtok.git "$env:LOCALAPPDATA\hermes\plugins\trimtok"

# Enable plugin in Hermes
hermes plugins enable trimtok
```

---

## Part of the Global Agent Trinity

**trimtok** operates standalone, but pairing it with **MemCore** and **Skill Proof** forms the **Global Agent Trinity** — connecting all your AI coding clients to three shared local hubs without open ports or daemons:

| Pillar | Hub | Role |
| :--- | :--- | :--- |
| **[trimtok](https://github.com/Stxyu-p/trimtok)** | `~/.trimtok` | **Context Shield:** Cuts terminal, traceback, diff, and JSON bloat by 38–48% (real telemetry) with instant `#c1` CCR recovery. |
| **[MemCore](https://github.com/Stxyu-p/memcore)** | `~/.memcore` | **Governed Memory:** Shared bitemporal memory with hard SQL scope boundaries, source-file SHA-256 tracking, and zero hallucinated drift. |
| **[Skill Proof](https://github.com/Stxyu-p/skill-proof)** | `~/.skill-proof` | **Skill Vault & Router:** Consolidates all `SKILL.md` files into one global vault and routes tasks in `< 2 ms` without LLM routing calls. |

```bash
# Install and link all three tools in one pass
pip install trimtok memcore-omnix skill-proof-router
trimtok install && memcore install && skill-proof install
```

---

## License

Distributed under the [MIT License](LICENSE). Designed and built for robust, real-world autonomous engineering.
