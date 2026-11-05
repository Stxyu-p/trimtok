# Trimtok Shield: Codebase Map & Bounded MCP Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an ultra-light, zero-disk-footprint codebase mapper (`trimtok map`) and a bounded JSON-RPC stdio proxy (`trimtok mcp-proxy`) to prune system prompt tool schemas and eliminate discovery overhead.

**Architecture:** A pure Python stdlib architecture with two independent modules: `codemap.py` extracts top-level symbols per directory within a 2,000-character budget using `git ls-files` + regex; `mcp_proxy.py` intercepts stdio JSON-RPC 2.0 frames between the harness and downstream MCP servers, filtering `tools/list` payloads and injecting ephemeral instructions at handshake without touching files on disk.

**Tech Stack:** Python 3.10+ (standard library only: `re`, `json`, `subprocess`, `sys`, `pathlib`, `shutil`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-trimtok-shield-ecosystem-design.md`

## Global Constraints

- **Stdlib Only:** No new dependencies added to `pyproject.toml` for these features.
- **Fail-Open Policy:** Any unparseable line, broken JSON, or process error passes through untouched.
- **Zero Disk Mutation:** Never modify user markdown files (`CLAUDE.md`, `AGENTS.md`) or repo files. All instructions pass through runtime MCP protocol or CLI output.
- **Cross-Platform:** Must support Windows 11 (handling UTF-8 BOM, forward-slash normalization, and EPIPE silence on child process exit).

## Review Focus

1. **UTF-8 BOM in piped stdio:** On Windows, shells can prepend `\ufeff`; proxy and mapper must strip it before parsing JSON or text.
2. **Content-Length vs Newline-Delimited JSON-RPC:** MCP transports can send raw JSON lines or LSP-style `Content-Length: ...\r\n\r\n{...}`; proxy must support both transparently.
3. **Child process crash / EPIPE:** When a downstream MCP server terminates early, the proxy must exit cleanly with the child's returncode without crashing with an unhandled BrokenPipeError.
4. **Budget truncation safety:** `codemap` must cleanly truncate at directory boundaries without producing dangling or invalid strings if budget is exceeded.
5. **Tools filtering integrity:** In `tools/list`, if all tools are filtered out, the proxy must return an empty list `[]` rather than removing the `tools` key or breaking schema.

---

### Task 1: Ultra-Light Codebase Map (`trimtok/codemap.py`)

**Files:**
- Create: `trimtok/codemap.py`
- Test: `tests/test_codemap.py`

**Interfaces:**
- Produces:
  ```python
  def build_codebase_map(
      root: str | Path | None = None,
      budget_chars: int = 2000,
      max_files: int = 4000,
  ) -> str:
      """Scan repository and return a compact single-line-per-directory symbol map."""
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codemap.py
from pathlib import Path
from trimtok.codemap import build_codebase_map

def test_codemap_extracts_symbols_within_budget(tmp_path: Path):
    # Setup dummy project
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("class Server:\n    pass\n\ndef run():\n    pass\n")
    (src / "utils.py").write_text("def helper():\n    pass\n")
    
    # Tests folder should be ignored
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_app.py").write_text("def test_run():\n    pass\n")

    result = build_codebase_map(root=tmp_path, budget_chars=500)
    assert "src/: " in result
    assert "Server" in result
    assert "run" in result
    assert "helper" in result
    assert "tests/" not in result
    assert len(result) <= 500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory C:/Users/gugmn/Workspace/trimtok --with pytest pytest tests/test_codemap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trimtok.codemap'`

- [ ] **Step 3: Implement `trimtok/codemap.py`**

Implement `build_codebase_map`:
- File discovery: Run `git ls-files` with timeout 3.0s; fallback to `os.walk` (max depth 6) if not a git repo.
- Filter out: `tests/`, `test_*`, `.*`, `node_modules`, `__pycache__`, `.venv`, `vendor`, `dist`, `build`.
- Match patterns:
  - Python: `^(?:async\s+)?def\s+([A-Za-z]\w*)`, `^class\s+([A-Za-z]\w*)`
  - JS/TS: `^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)`
  - Go: `^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)`, `^type\s+([A-Z]\w*)`
  - Rust: `^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type)\s+(\w+)`
- Sort directories prioritizing shared paths (`util`, `helper`, `core`, `model`, `component`).
- Build one line per directory: `{dir}/: {sym1}, {sym2}...`.
- Enforce `budget_chars`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory C:/Users/gugmn/Workspace/trimtok --with pytest pytest tests/test_codemap.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/gugmn/Workspace/trimtok add trimtok/codemap.py tests/test_codemap.py
git -C C:/Users/gugmn/Workspace/trimtok commit -m "feat(codemap): add ultra-light codebase symbol mapper"
```

---

### Task 2: Bounded Stdio JSON-RPC MCP Proxy (`trimtok/mcp_proxy.py`)

**Files:**
- Create: `trimtok/mcp_proxy.py`
- Test: `tests/test_mcp_proxy.py`

**Interfaces:**
- Produces:
  ```python
  def filter_mcp_message(
      raw_bytes: bytes,
      allow_tools: set[str] | None = None,
      deny_tools: set[str] | None = None,
      instructions: str | None = None,
  ) -> bytes:
      """Filter tools in tools/list response or inject instructions in initialize response."""

  def run_mcp_proxy(
      command: list[str],
      allow_tools: set[str] | None = None,
      deny_tools: set[str] | None = None,
      instructions: str | None = None,
  ) -> int:
      """Run subprocess and proxy stdio with JSON-RPC filtering."""
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_proxy.py
import json
from trimtok.mcp_proxy import filter_mcp_message

def test_filter_mcp_tools_list():
    sample_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "core_read", "description": "Read file"},
                {"name": "admin_purge", "description": "Purge cache"},
                {"name": "admin_doctor", "description": "Doctor check"}
            ]
        }
    }
    raw = json.dumps(sample_response).encode("utf-8")
    filtered = filter_mcp_message(raw, allow_tools={"core_read"})
    data = json.loads(filtered.decode("utf-8"))
    
    tools = [t["name"] for t in data["result"]["tools"]]
    assert tools == ["core_read"]
    assert "admin_purge" not in tools

def test_filter_mcp_initialize_instructions():
    sample_init = {
        "jsonrpc": "2.0",
        "id": 0,
        "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "test", "version": "1.0"},
            "capabilities": {}
        }
    }
    raw = json.dumps(sample_init).encode("utf-8")
    filtered = filter_mcp_message(raw, instructions="Use trimtok retrieve")
    data = json.loads(filtered.decode("utf-8"))
    assert data["result"]["instructions"] == "Use trimtok retrieve"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory C:/Users/gugmn/Workspace/trimtok --with pytest pytest tests/test_mcp_proxy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trimtok.mcp_proxy'`

- [ ] **Step 3: Implement `trimtok/mcp_proxy.py`**

Implement:
- `filter_mcp_message(raw_bytes, allow_tools, deny_tools, instructions)`:
  - Supports both newline JSON and `Content-Length: <N>\r\n\r\n<JSON>`.
  - Strips UTF-8 BOM if present.
  - Checks if response contains `result.tools`: filters list by `allow_tools` (if given) and `deny_tools` (if given).
  - Checks if response contains `result.serverInfo` or `protocolVersion` (`initialize` response): sets or appends `result["instructions"] = instructions`.
  - Returns modified bytes (with updated `Content-Length` header if original used header format).
  - Fail-open: returns original bytes on any JSON parse error.
- `run_mcp_proxy(command, allow_tools, deny_tools, instructions)`:
  - Spawns child process with `command`.
  - Threads or non-blocking loop piping `sys.stdin -> child.stdin` (passthrough) and `child.stdout -> filter_mcp_message -> sys.stdout`.
  - Silently catches `BrokenPipeError` / `EPIPE`.
  - Returns `child.wait()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory C:/Users/gugmn/Workspace/trimtok --with pytest pytest tests/test_mcp_proxy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/gugmn/Workspace/trimtok add trimtok/mcp_proxy.py tests/test_mcp_proxy.py
git -C C:/Users/gugmn/Workspace/trimtok commit -m "feat(mcp-proxy): add bounded JSON-RPC stdio proxy"
```

---

### Task 3: CLI Subcommands & Native MCP Instructions Integration

**Files:**
- Modify: `trimtok/__main__.py`
- Modify: `trimtok/mcp_server.py`
- Test: `tests/test_cli_extensions.py`

**Interfaces:**
- Consumes:
  - `trimtok.codemap.build_codebase_map`
  - `trimtok.mcp_proxy.run_mcp_proxy`
- Produces:
  - CLI: `trimtok map [path] [--budget <chars>]`
  - CLI: `trimtok mcp-proxy [--allow <csv>] [--deny <csv>] [--instructions <text>] -- <command...>`
  - MCP: `trimtok.mcp_server` `initialize` response returning `SERVER_INSTRUCTIONS`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_extensions.py
import subprocess
import sys

def test_cli_map_runs():
    res = subprocess.run(
        [sys.executable, "-m", "trimtok", "map", "--budget", "500"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "Codebase map" in res.stdout or res.stdout.strip() == ""

def test_mcp_server_instructions_present():
    from trimtok import mcp_server
    assert hasattr(mcp_server, "SERVER_INSTRUCTIONS")
    assert "trimtok retrieve" in mcp_server.SERVER_INSTRUCTIONS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory C:/Users/gugmn/Workspace/trimtok --with pytest pytest tests/test_cli_extensions.py -v`
Expected: FAIL with unrecognized arguments or missing attributes

- [ ] **Step 3: Update `trimtok/__main__.py` & `trimtok/mcp_server.py`**

- In `trimtok/mcp_server.py`:
  - Define `SERVER_INSTRUCTIONS = "When retrieving compressed terminal or tool output, call trimtok_retrieve with the ref ID (e.g. #c1). For specific sections, specify 'errors', 'diff', 'summary', or 'tail'."`
  - In `handle_message` for method `initialize`, ensure `result["instructions"] = SERVER_INSTRUCTIONS`.
- In `trimtok/__main__.py`:
  - Add subparser `map` with arguments `path` (default ".") and `--budget` (default 2000).
  - Add subparser `mcp-proxy` with `--allow`, `--deny`, `--instructions`, and `command` (remainder args).
  - Dispatch handlers for `map` and `mcp-proxy`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --directory C:/Users/gugmn/Workspace/trimtok --with pytest pytest tests/test_cli_extensions.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite regression**

Run: `uv run --directory C:/Users/gugmn/Workspace/trimtok --with pytest pytest tests/test_version_sync.py tests/test_core.py tests/test_codemap.py tests/test_mcp_proxy.py tests/test_cli_extensions.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/gugmn/Workspace/trimtok add trimtok/__main__.py trimtok/mcp_server.py tests/test_cli_extensions.py
git -C C:/Users/gugmn/Workspace/trimtok commit -m "feat(cli): wire map and mcp-proxy subcommands with native MCP instructions"
```
