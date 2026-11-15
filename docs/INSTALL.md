# Install and wiring

What this page answers: which install channel to pick, what `trimtok install`
actually changes on your disk, and what you still have to edit by hand.

Everything below was read from the code (`trimtok/mcp_install.py`,
`trimtok/__main__.py`) and from a live run on Windows. Paths for macOS and Linux
differ only where noted.

## 1. Pick a channel

| Channel | Command | Use when |
| --- | --- | --- |
| pip | `pip install trimtok` | You already have a project venv and want it there. |
| pipx | `pipx install trimtok` | You want the `trimtok` CLI on PATH without polluting a venv. |
| uv tool | `uv tool install trimtok` | Same as pipx, faster. |
| uvx (no install) | `uvx --from trimtok trimtok-mcp` | You only need the MCP server and would rather not install anything. Use this string as the `command` in a client config. |
| git clone | `git clone https://github.com/Stxyu-p/trimtok && pip install -e trimtok` | You are developing it, or installing it as a Hermes plugin (Hermes loads plugin code from its own snapshot, so a clone is the normal path there). |

There is deliberately no Docker image, no Homebrew formula and no npm package. The
compression runs **in-process inside the agent's own hook**, so there is no daemon
and no port to containerise or wrap; those channels would be maintenance for a
delivery mechanism that does not exist.

Whichever channel you pick, the MCP entry records **the interpreter that ran
`trimtok install`** (see §3). That is the one thing that bites people.

## 2. What `trimtok install` changes

```
trimtok install                  # every detected client
trimtok install --dry-run        # show what would change, write nothing
trimtok install --client cursor  # one client (repeatable)
trimtok install --print          # print a generic mcpServers snippet and exit
```

It writes exactly five things and nothing else — no PATH edit, no service, no
environment variables:

| # | What | Where |
| -- | --- | --- |
| 1 | MCP server entry per detected client | each client's own config file (§4) |
| 2 | A skill file | `~/.skill-proof/skills/trimtok/SKILL.md` |
| 3 | Antigravity CLI schema cache | `~/.gemini/antigravity-cli/mcp/trimtok/*.json` (only if that directory exists) |
| 4 | The CCR store (first run) | `~/.trimtok/trimtok_ccr_v2.sqlite3` |
| 5 | Nothing else | — |

`install` finishes by printing a readiness line (store path, chunk count, whether
Zstandard and RapidFuzz are active) and the reminder to **restart the agent
client** — most hosts read MCP configs only at startup.

## 3. The interpreter trap

`trimtok install --print` on this machine produced:

```json
{
  "mcpServers": {
    "trimtok": {
      "command": "C:\\Users\\<user>\\AppData\\Local\\hermes\\installs\\<id>\\environments\\<id>\\venv\\Scripts\\python.exe",
      "args": ["-m", "trimtok.mcp_server"]
    }
  }
}
```

That absolute path is `sys.executable` — whatever Python was active when you ran
the command. Consequences:

- If that interpreter lives in a venv you later delete, rebuild or move, **every
  client's trimtok entry dies** with `command not found`, and the agent silently
  loses the tools.
- If you installed with `uv tool` or `pipx`, the path points into their tool venv.
  That is fine and stable — until you `pipx upgrade` into a new venv.
- If you want a config that survives all of that, use the zero-install form:

```json
{
  "mcpServers": {
    "trimtok": {
      "command": "uvx",
      "args": ["--from", "trimtok", "trimtok-mcp"]
    }
  }
}
```

`uvx` re-resolves the package on each start, so no absolute path is pinned. The
cost is a little startup latency the first time after an upgrade.

## 4. Per-client config, by hand

`trimtok install` handles all of these when it detects them. Use this table when
detection fails (`not detected` in `trimtok clients`), or when you want to write
the entry yourself. On Windows the config root is `%APPDATA%`; on macOS
`~/Library/Application Support`; on Linux `~/.config`.

| Client key | Config file | Key to add under | Entry format |
| --- | --- | --- | --- |
| `claude-code` | `~/.claude.json` | `mcpServers` | JSON, typed (`"type": "stdio"`) |
| `claude-desktop` | `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` | JSON |
| `cursor` | `~/.cursor/mcp.json` | `mcpServers` | JSON |
| `windsurf` | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` | JSON |
| `gemini-cli` | `~/.gemini/settings.json` | `mcpServers` | JSON |
| `antigravity` | `~/.gemini/config/mcp_config.json` | `mcpServers` | JSON |
| `antigravity-ide` | `~/.gemini/antigravity/mcp_config.json` | `mcpServers` | JSON |
| `vscode` | `%APPDATA%\Code\User\mcp.json` | **`servers`** | JSON, typed |
| `cline` | `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` | `mcpServers` | JSON |
| `codex` | `~/.codex/config.toml` | **`[mcp_servers.trimtok]`** | TOML |
| `opencode` | `~/.config/opencode/opencode.json` | **`mcp`** | JSON, `command` is an **array** |

Two formats are not the usual `mcpServers` shape:

**Codex** (`~/.codex/config.toml`) — append:

```toml
[mcp_servers.trimtok]
command = "uvx"
args = ["--from", "trimtok", "trimtok-mcp"]
```

**OpenCode** (`~/.config/opencode/opencode.json`) — `command` is a single array,
not `command` + `args`:

```json
{
  "mcp": {
    "trimtok": {
      "type": "local",
      "command": ["uvx", "--from", "trimtok", "trimtok-mcp"],
      "enabled": true
    }
  }
}
```

**Claude Code** is special: the running app rewrites `~/.claude.json`, so writing
that file behind its back gets lost. trimtok shells out instead:

```bash
claude mcp add-json --scope user trimtok '{"type":"stdio","command":"uvx","args":["--from","trimtok","trimtok-mcp"]}'
claude mcp get trimtok     # verify
```

## 5. Hooks vs MCP — what actually compresses, where

This is the part the feature list does not make obvious, so it is spelled out:

| Host | Automatic in-path compression | Tools available |
| --- | --- | --- |
| **Hermes** | **Yes** — plugin hooks (`transform_terminal_output`, `transform_tool_result`, `post_llm_call`, `on_session_start`, `on_session_end`) fire on every tool result | `trimtok_retrieve`, `trimtok_exec` |
| Every other client (Claude Code, Cursor, Codex, Gemini, Antigravity, Windsurf, VS Code, Cline, OpenCode, Claude Desktop) | **No hook is installed** — compression happens when the model calls a tool | `trimtok_compress_terminal`, `trimtok_compress_tool`, `trimtok_retrieve`, `trimtok_exec`, `trimtok_status` |

So on a non-Hermes host, `trimtok install` gives the agent the *tools*; it does not
rewrite tool results behind the agent's back. Two host-agnostic ways to get the
in-path behaviour without hooks:

```bash
trimtok wrap -- pytest -q          # run a command, compress its output
trimtok mcp-proxy -- <server cmd>  # bounded stdio proxy that prunes tool schemas
```

To get true automatic compression on Hermes, install it as a plugin (§6).

## 6. Hermes plugin (the only automatic path today)

```bash
git clone https://github.com/Stxyu-p/trimtok ~/.hermes/plugins/trimtok
```

Hermes loads plugin code from its own snapshot, not from that directory, so after
editing or pulling you must rebuild the workspace (or reinstall the plugin) before
the change is live. Confirm which bytes are running with:

```bash
python -c "import importlib.util; print(importlib.util.find_spec('trimtok').origin)"
```

and confirm the running version from the newest row of `~/.trimtok/usage.jsonl`
(`"version"` field) — a subprocess import always re-reads disk and proves nothing
about the live process.

## 7. Verify

```bash
trimtok clients                 # registered / not registered / not detected, with paths
trimtok status                  # store path, chunk count, savings
trimtok compress README.md      # one-shot compression test on a real file
echo '{}' | trimtok mcp         # JSON-RPC server starts and answers on stdio
```

A registered client that shows no tools after restart almost always has a broken
`command` path — re-read the entry and run that exact path manually.

## 8. Uninstall

```bash
trimtok uninstall                       # removes the MCP entry from every client
trimtok uninstall --client cursor       # or one of them
trimtok purge --older-than 24           # drop cached originals
```

`uninstall` removes the MCP registration only. It leaves the CCR store
(`~/.trimtok/`) and the skill file (`~/.skill-proof/skills/trimtok/`) in place —
delete those two paths yourself if you want a clean machine.

## 9. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `trimtok clients` says `not detected` | The client's config directory does not exist yet | Start the client once, or pass `--client <key>` explicitly |
| Client shows the server but no tools | `command` points at a deleted venv | Rewrite the entry with `uvx --from trimtok trimtok-mcp` |
| Claude Code loses the entry after a restart | You edited `~/.claude.json` by hand while the app was running | Use `claude mcp add-json` |
| Everything works but savings are 0 | No hook is installed on that host (§5) | Use `trimtok wrap`, or install the Hermes plugin |
| Store grows without bound | Nothing purges it automatically | `trimtok purge --older-than <hours>` or `--max-mb` |
