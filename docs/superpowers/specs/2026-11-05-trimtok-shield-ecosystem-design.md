# Trimtok Shield Ecosystem: Bounded MCP Proxy, Universal Wiring & Pre-Seeded Context

**Status:** Proposed  
**Author:** Mika (Strategic Consigliere) & พี่โชค  
**Target:** trimtok (v5.4.0 roadmap)  
**Date:** 2026-11-05  

---

## 1. Intent & The Why

`trimtok` (v5.3.5) is currently the fastest, most effective in-process context compression engine for coding agent payloads (sub-2ms, Delta engine, AST skeletonizer, TOON v2, CCR recovery). However, payload compression only guards **turn outputs**; it leaves two massive token-drain vectors completely unprotected:
1. **System Prompt / Tool Schema Bloat:** Upstream MCP servers expose sprawling administrative, diagnostic, and exploratory tool definitions (e.g., ProjectMem exposes 17 tools, Context-Mode exposes 11 tools). Every turn pays an upfront token tax on schemas that the agent never uses during normal coding.
2. **Setup Friction & Fragmented Wiring:** While `trimtok` has `mcp_install.py` for client registration, users still manually wire rule files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) and pre-execution hooks across harnesses (Claude Code, Antigravity, Codex, Cursor, OpenCode).

By adapting the strongest concepts from `HoangP8/tokless`—specifically its **Bounded MCP Proxy** and **Unified Multi-Harness Wiring**—`trimtok` expands from an execution-stage compressor into a complete **360° Context Lifecycle Shield**.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Tool Schema Pruning via Bounded Stdio Proxy (`trimtok mcp-proxy`):** Intercept JSON-RPC `tools/list` between harness and any stdio MCP server, filtering tools by explicit `--allow` or `--deny` lists and optionally injecting compact server instructions.
- **Universal Multi-Harness Wiring (`trimtok wire` / extended `trimtok install`):** Idempotently detect and configure instruction files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, Cursor rules) with managed fences (`<!-- trimtok:begin -->` ... `<!-- trimtok:end -->`) and shell wrappers.
- **Push-over-Pull Project Context:** Pre-seed stack constraints and known gotchas at session start to eliminate redundant tool query turns.
- **Zero New Heavy Dependencies:** Implemented in pure Python (stdlib only) consistent with `mcp_install.py` and `mcp_server.py`.

### Non-Goals
- **No HTTP Proxy Daemon:** We explicitly reject Headroom-style persistent HTTP daemons or opening TCP ports (8787/18787). `trimtok` remains 100% portless and stdio/in-process.
- **No Lossy Command Truncation:** We reject RTK-style silent line discarding. All compressed outputs remain 100% CCR-backed.

### Anti-Goals
- Overwriting or corrupting foreign user configuration in `settings.json`, `config.toml`, or instruction files.
- Introducing latency > 5ms into stdio MCP communication.

---

## 3. Constraints

1. **Host Environments:** Cross-platform (Windows 11, macOS, Linux). Must support Windows carriage returns, path styles, and UTF-8 encoding.
2. **Python Compatibility:** Python 3.10+ stdlib only for wiring and proxying; no heavy external packages for the CLI glue.
3. **Fail-Open Policy:** If `trimtok mcp-proxy` encounters a broken JSON message or child process failure, it passes raw stdio through without crashing the agent harness.
4. **Idempotency:** Re-running `trimtok wire` or `trimtok install` N times must produce the exact same byte output without duplicate blocks.

---

## 4. Approaches Considered & Trade-offs

| Approach | Description | Pros | Cons / Trade-offs | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **A: In-Harness Native Plugins Only** | Rely solely on harness-specific extension APIs (e.g. Hermes plugin, VS Code extension). | Deepest access to agent internal AST. | Zero leverage across CLI tools (Codex, Antigravity, Claude Code CLI). | Rejected as sole strategy. |
| **B: Standalone HTTP Gateway Proxy** | Run a local HTTP daemon (like Headroom/9router) intercepting all API traffic. | Can compress prompts before network transmission. | 9s latency, network port conflicts, auth token management headaches. | **Anti-goal. Strongly Rejected.** |
| **C: Lightweight Stdio JSON-RPC Filter + Managed Rule Wiring (Tokless-inspired, Trimtok-native)** | Stdio process wrapper intercepting MCP JSON-RPC, paired with idempotent instruction injection. | Portless, microsecond latency, works across all 17 harnesses instantly. | Stdio process spawn overhead (negligible in Python, <15ms). | **Selected Approach.** |

---

## 5. Detailed Technical Design

### Phase 1: Universal Wiring Engine (`trimtok wire`)
Extend `trimtok/mcp_install.py` and `trimtok/__main__.py`:
1. **Target Matrix:**
   - Claude Code: `~/.claude/CLAUDE.md` + `~/.claude.json`
   - Codex CLI: `~/.codex/AGENTS.md` + `~/.codex/config.toml`
   - Antigravity / Gemini CLI: `~/.gemini/GEMINI.md` + `~/.gemini/antigravity/mcp_config.json`
   - OpenCode: `~/.config/opencode/AGENTS.md` + `opencode.json`
   - Cursor: `.cursor/rules/trimtok.mdc` or `~/.cursor/mcp.json`
2. **Instruction Block Format:**
   ```markdown
   <!-- trimtok:begin v1 -->
   ## Context Optimization (trimtok)
   - When retrieving full terminal or tool output, use: `trimtok retrieve <ref>`
   - To inspect specific sections: `trimtok retrieve <ref> errors|diff|summary|tail`
   <!-- trimtok:end -->
   ```
3. **Atomic Operations:** Reads existing instruction files, replaces existing fence if present, or appends cleanly. If unwired (`trimtok unwire`), strips the block cleanly without touching surrounding user rules.

### Phase 2: Bounded MCP Proxy (`trimtok mcp-proxy`)
Create `trimtok/mcp_proxy.py`:
1. **Invocation:**
   ```bash
   trimtok mcp-proxy --allow "codegraph_explore" -- npx -y @colbymchenry/codegraph
   trimtok mcp-proxy --deny "ctx_doctor,ctx_stats,ctx_purge" -- npx -y context-mode
   ```
2. **JSON-RPC Interception Loop:**
   - Reads lines from `sys.stdin` (newline-delimited JSON-RPC or Content-Length headers).
   - Passes all requests down to the child process unchanged.
   - For responses to `tools/list`:
     - Parses the `result.tools` array.
     - Applies whitelist/blacklist filtering.
     - Serializes back and writes to `sys.stdout`.
   - For `initialize` response:
     - Optionally injects compact server instruction overrides.
   - All other requests/notifications stream verbatim with zero parsing overhead.

### Phase 3: Push-over-Pull Context Seeding
Provide a lightweight stack detector (`trimtok seed` / `trimtok stack`) that extracts high-level project facts (e.g., Python version, package manager, test framework) and generates a 3-line Markdown snippet for session start hooks or instruction files, saving initial discovery turns.

---

## 6. Verification & Test Plan

1. **Unit Tests (`tests/test_mcp_proxy.py`):**
   - Mock JSON-RPC `tools/list` request & response: verify tools outside `--allow` are stripped.
   - Test malformed JSON fallback (fail-open guarantee).
   - Test notification and streaming passthrough with zero corruption.
2. **Wiring Idempotency Tests (`tests/test_wire.py`):**
   - Test clean write, update of existing fence, and `unwire` removal on dummy instruction files.
   - Verify foreign markdown content is untouched.
3. **End-to-End CLI Verification:**
   - Run `trimtok wire --dry-run` to inspect generated diffs across detected agent configs on the machine.

---

## 7. Approval & Next Step

Once approved by พี่โชค, we proceed directly to **`superpowers:writing-plans`** to produce the exact step-by-step implementation tasks.
