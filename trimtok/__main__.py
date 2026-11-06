#!/usr/bin/env python3
"""Universal CLI for Trimtok.

Usage:
  trimtok install [--client <name>] [--dry-run] [--print]   # Auto-register MCP server in all detected agent CLIs/IDEs
  trimtok uninstall [--client <name>] [--dry-run]           # Remove MCP server registration
  trimtok clients                                           # List all supported agent clients and registration status
  trimtok mcp                                               # Start the stdio MCP server
  trimtok status | usage | purge                            # Inspect savings, usage stats, or purge CCR cache
  trimtok retrieve <ref> [errors|diff|summary|tail]         # Retrieve original or section from CCR
  trimtok compress [--cmd <cmd>] [--tool <name>] [file|-]   # Compress terminal or tool text from file or stdin
"""

from __future__ import annotations

from typing import Any

import argparse
import json
import sys
from pathlib import Path

try:
    from . import ccr
    from .core import VERSION, compress_terminal
    from .dedup import compress_tool_result
    from .__init__ import _handle_retrieve, _handle_ts_command
    from .mcp_install import ServerSpec, add_install_arguments, run_clients, run_install
    from . import mcp_server
except ImportError:
    import ccr  # type: ignore
    from core import VERSION, compress_terminal  # type: ignore
    from dedup import compress_tool_result  # type: ignore
    from __init__ import _handle_retrieve, _handle_ts_command  # type: ignore
    from mcp_install import ServerSpec, add_install_arguments, run_clients, run_install  # type: ignore
    import mcp_server  # type: ignore


def _server_spec() -> ServerSpec:
    return ServerSpec(name="trimtok", module="trimtok.mcp_server")


def _sync_antigravity_schema_cache() -> None:
    """Populate ~/.gemini/antigravity-cli/mcp/trimtok/*.json and instructions.md if Antigravity CLI is present."""
    try:
        ag_mcp = Path.home() / ".gemini" / "antigravity-cli" / "mcp"
        if not ag_mcp.parent.is_dir():
            return
        target = ag_mcp / "trimtok"
        target.mkdir(parents=True, exist_ok=True)
        for tool in mcp_server.TOOLS:
            schema = {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            }
            (target / f"{tool['name']}.json").write_text(
                json.dumps(schema, ensure_ascii=False), encoding="utf-8"
            )
        if getattr(mcp_server, "SERVER_INSTRUCTIONS", None):
            (target / "instructions.md").write_text(
                mcp_server.SERVER_INSTRUCTIONS.strip() + "\n", encoding="utf-8"
            )
    except Exception:
        pass


def _sync_global_skill() -> None:
    """Install SKILL.md into ~/.skill-proof/skills/trimtok and client skill directories if present."""
    skill_md = (
        "---\n"
        "name: trimtok\n"
        "description: >-\n"
        "  Deterministic context compressor (Trimtok) for terminal outputs, tool results,\n"
        "  deep tracebacks, unified git diffs, AST code skeletons, and JSON payloads (TOON v2), backed by a two-tier\n"
        "  SQLite WAL Content-Controlled Retrieval (CCR) cache with section-based retrieval.\n"
        "---\n\n"
        "# Trimtok — Context Compression & Section Retrieval\n\n"
        "Use `trimtok_compress_terminal`, `trimtok_compress_tool`, `trimtok_retrieve`, and `trimtok_status` "
        "to compress noisy outputs or large files on disk (`file_path`) and retrieve exact sections (`errors`, `diff`, `summary`, `tail`).\n"
    )
    for root in (
        Path.home() / ".skill-proof" / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".gemini" / "config" / "skills",
    ):
        try:
            if root.is_dir():
                d = root / "trimtok"
                d.mkdir(parents=True, exist_ok=True)
                target = d / "SKILL.md"
                if not target.exists():
                    target.write_text(skill_md, encoding="utf-8")
        except Exception:
            pass


def _run_setup_wizard() -> None:
    """Initialize global CCR SQLite WAL store, sync schemas/skills, and run live self-check."""
    _sync_antigravity_schema_cache()
    _sync_global_skill()
    try:
        store_dir = ccr.default_dir()
        st = ccr.stats()
        zstd_ok = getattr(ccr, "_ZSTD_AVAILABLE", False)
        fuzzy_ok = getattr(__import__("trimtok.core", fromlist=["_FUZZY_AVAILABLE"]), "_FUZZY_AVAILABLE", False)
        print(
            f"[Trimtok Setup Wizard] Ready | Store: {store_dir / 'trimtok_ccr_v2.sqlite3'} "
            f"({st.get('entries', 0)} chunks) | Zstd: {'yes' if zstd_ok else 'zlib'} | "
            f"RapidFuzz: {'yes' if fuzzy_ok else 'difflib'}"
        )
    except Exception as e:
        print(f"[Trimtok Setup Wizard] Notice: store stats unavailable ({e})")


def _run_purge(ns: Any) -> int:
    """Explicit CCR retention: age-based purge, then a hard size ceiling."""
    from . import ccr as _ccr

    hours = float(getattr(ns, "older_than", 0.0) or 0.0)
    max_mb = float(getattr(ns, "max_mb", 0.0) or 0.0)

    before = _ccr.stats()

    purged = 0
    if hours > 0:
        purged = _ccr.purge(before_seconds=hours * 3600.0)
        print(f"Purged {purged} entries older than {hours:g}h")
    elif max_mb <= 0:
        # A bare `purge` means "clear it"; asking only for a size cap must not
        # wipe the store before it measures it.
        purged = _ccr.purge()
        print(f"Purged {purged} entries")

    if max_mb > 0:
        limit = int(max_mb * 1024 * 1024)
        try:
            evicted = _ccr.evict_to_size(limit)
            print(f"Evicted {evicted} oldest entries to stay under {max_mb:g} MB")
        except Exception as exc:
            print(f"Size cap not applied: {exc}")

    after = _ccr.stats()
    print(f"CCR: {before.get('entries', 0)} -> {after.get('entries', 0)} entries, "
          f"{after.get('bytes', 0) / 1048576:.2f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args_list = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="trimtok",
        description=f"Trimtok v{VERSION} — Deterministic Context Compressor & Universal MCP Server",
    )
    parser.add_argument("--version", action="version", version=f"trimtok {VERSION}")
    sub = parser.add_subparsers(dest="cmd")

    p_inst = sub.add_parser("install", help="Run Setup Wizard & auto-register Trimtok MCP server across agent CLIs/IDEs")
    add_install_arguments(p_inst)

    p_setup = sub.add_parser("setup", help="Run full Trimtok Setup Wizard (initialize store, sync skills & register MCP clients)")
    add_install_arguments(p_setup)

    p_uninst = sub.add_parser("uninstall", help="Remove Trimtok MCP server registration from agent CLIs/IDEs")
    add_install_arguments(p_uninst)

    sub.add_parser("clients", help="List supported agent clients and Trimtok registration status")
    sub.add_parser("mcp", help="Run the stdio MCP server (JSON-RPC 2.0)")
    sub.add_parser("status", help="Show compression savings and CCR cache status")
    sub.add_parser("usage", help="Show detailed hook/tool usage breakdown")
    sub.add_parser("gain", help="Show detailed token savings gain report").add_argument("--days", type=int, default=0)
    sub.add_parser("discover", help="Scan telemetry for low-savings shapes and suggest filters")
    sub.add_parser("recall", help="Retrieve cached original by hash").add_argument("hash", nargs="?", default="")
    p_purge = sub.add_parser("purge", help="Purge cached CCR originals (all, or entries older than --older-than)")
    p_purge.add_argument("--older-than", type=float, default=0.0,
                         help="Only purge entries older than N hours (default: everything)")
    p_purge.add_argument("--max-mb", type=float, default=0.0,
                         help="After purging, evict oldest entries until the store is under N MB")

    p_ret = sub.add_parser("retrieve", help="Retrieve original content or a targeted section from CCR")
    p_ret.add_argument("ref", help="CCR reference (#c1 or 12-hex digest)")
    p_ret.add_argument("section", nargs="?", choices=["errors", "diff", "summary", "tail"], help="Optional section")
    p_ret.add_argument("--grep", help="Optional safe case-insensitive regex filter")
    p_ret.add_argument("--intent", help="Free-text task; returns the lines ranked most relevant")
    p_ret.add_argument("--intent-budget", type=int, default=400, help="Max ranked lines (default 400)")
    p_ret.add_argument("--offset", type=int, help="1-indexed start line")
    p_ret.add_argument("--limit", type=int, help="Maximum lines to return")

    p_search = sub.add_parser("search", help="Search stored CCR payloads via SQLite FTS5 BM25")
    p_search.add_argument("query", help="Keyword or phrase to search across all stored payloads")
    p_search.add_argument("--limit", type=int, default=5, help="Maximum matching payloads to return (default: 5)")

    p_comp = sub.add_parser("compress", help="Compress terminal or tool text from a file or stdin")
    p_comp.add_argument("input", nargs="?", default="-", help="File path or '-' for stdin")
    p_comp.add_argument("--command", default="", help="Shell command name (for terminal mode)")
    p_comp.add_argument("--tool", default="", help="Tool name (switches to tool/TOON mode)")
    p_comp.add_argument("--min-chars", type=int, default=2048, help="Minimum char threshold (default: 2048)")

    p_wrap = sub.add_parser("wrap", help="Run a command and compress its terminal output via Trimtok")
    p_wrap.add_argument("command", nargs=argparse.REMAINDER, help="Command and arguments to run")

    p_rew = sub.add_parser(
        "rewrite",
        help="Print the noise-reduced form of a command (for shell aliases; Hermes has no pre-exec hook)",
    )
    p_rew.add_argument("command", nargs=argparse.REMAINDER, help="Command to rewrite")

    p_map = sub.add_parser("map", help="Scan repository and output compact codebase symbol map (budgeted)")
    p_map.add_argument("path", nargs="?", default=".", help="Directory to scan (default: current)")
    p_map.add_argument("--budget", type=int, default=2000, help="Max character budget (default: 2000)")
    p_map.add_argument("--max-files", type=int, default=4000, help="Max files to scan (default: 4000)")
    p_map.add_argument("--focus", help="Focus file path to prioritize its symbols and direct dependencies")

    p_mcp_proxy = sub.add_parser("mcp-proxy", help="Run bounded stdio MCP proxy filtering tools/list and injecting instructions")
    p_mcp_proxy.add_argument("--allow", help="Comma-separated allowlist of tool names")
    p_mcp_proxy.add_argument("--deny", help="Comma-separated denylist of tool names")
    p_mcp_proxy.add_argument("--instructions", help="Optional instruction string override for initialize response")
    p_mcp_proxy.add_argument("--kv-lock", action="store_true", help="Stabilize instructions for KV-cache alignment")
    p_mcp_proxy.add_argument("command", nargs=argparse.REMAINDER, help="MCP server command and arguments to run")

    p_kv = sub.add_parser("kv-lock", help="Stabilize prompt content for KV-cache alignment")
    p_kv.add_argument("input", nargs="?", default="-", help="File path or '-' for stdin")

    ns = parser.parse_args(args_list)

    if ns.cmd in (None, "status"):
        print(_handle_ts_command("status"))
        print(f"Data directory: {ccr.default_dir()}")
        return 0
    if ns.cmd == "usage":
        print(_handle_ts_command("usage"))
        return 0
    if ns.cmd == "gain":
        days = getattr(ns, "days", 0)
        print(_handle_ts_command(f"gain {days}" if days else "gain"))
        return 0
    if ns.cmd == "discover":
        print(_handle_ts_command("discover"))
        return 0
    if ns.cmd == "recall":
        h = getattr(ns, "hash", "")
        print(_handle_ts_command(f"retrieve {h}" if h else "retrieve"))
        return 0
    if ns.cmd == "purge":
        return _run_purge(ns)
    if ns.cmd == "mcp":
        return mcp_server.main()
    if ns.cmd == "clients":
        return run_clients(_server_spec())
    if ns.cmd in ("install", "setup"):
        rc = run_install(_server_spec(), ns, remove=False)
        if not getattr(ns, "dry_run", False) and not getattr(ns, "print_only", False):
            _run_setup_wizard()
        return rc
    if ns.cmd == "uninstall":
        return run_install(_server_spec(), ns, remove=True)
    if ns.cmd == "retrieve":
        q: dict = {"hash": ns.ref}
        if ns.section:
            q["section"] = ns.section
        if ns.grep:
            q["grep"] = ns.grep
        if getattr(ns, "intent", None):
            q["intent"] = ns.intent
            q["intent_budget"] = ns.intent_budget
        if ns.offset is not None:
            q["offset"] = ns.offset
        if ns.limit is not None:
            q["limit"] = ns.limit
        out = _handle_retrieve(q)
        print(out)
        try:
            res_obj = json.loads(out)
            if res_obj.get("status") == "error":
                return 1
        except Exception:
            pass
        return 0
    if ns.cmd == "search":
        if not ccr.fts_ready():
            sys.stderr.write("Error: FTS5 search is unavailable in this SQLite build.\n")
            return 2
        hits = ccr.search(ns.query, limit=ns.limit)
        print(json.dumps({"query": ns.query, "count": len(hits), "hits": hits}, indent=2, ensure_ascii=False))
        return 0
    if ns.cmd == "compress":
        raw = sys.stdin.read() if ns.input == "-" else Path(ns.input).read_text(encoding="utf-8", errors="replace")
        if ns.tool:
            compressed, stats = compress_tool_result(ns.tool, raw, min_chars=ns.min_chars)
        else:
            compressed, stats = compress_terminal(ns.command, raw, min_chars=ns.min_chars)
        if stats.get("gate") == "ok":
            ref = ccr.put(raw)
            if ref and stats.get("digest"):
                compressed = compressed.replace(f"#{stats['digest']}", ref)
            elif not ref and not stats.get("lossless_tier0"):
                compressed = raw
        sys.stdout.write(compressed)
        if not compressed.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    if ns.cmd == "rewrite":
        cmd_args = list(ns.command)
        if cmd_args and cmd_args[0] == "--":
            cmd_args = cmd_args[1:]
        if not cmd_args:
            sys.stderr.write("Error: no command provided. Usage: trimtok rewrite -- <command> [args...]\n")
            return 1
        from .prerewrite import rewrite_argv
        import shlex
        sys.stdout.write(shlex.join(rewrite_argv(cmd_args)) + "\n")
        return 0
    if ns.cmd == "wrap":
        cmd_args = list(ns.command)
        if cmd_args and cmd_args[0] == "--":
            cmd_args = cmd_args[1:]
        if not cmd_args:
            sys.stderr.write("Error: no command provided to wrap. Usage: trimtok wrap -- <command> [args...]\n")
            return 1
        import subprocess
        from .prerewrite import rewrite_argv
        # Pre-execution command rewriting (e.g. pytest -> pytest -q --tb=short).
        # Operates on argv so the caller's quoting survives.
        cmd_args = rewrite_argv(cmd_args)
        proc = subprocess.run(cmd_args, capture_output=True, text=True, shell=False)
        combined = (proc.stdout or "") + (proc.stderr or "")
        cmd_name = Path(cmd_args[0]).stem
        compressed, stats = compress_terminal(cmd_name, combined, min_chars=512)
        if stats.get("gate") == "ok":
            ref = ccr.put(combined)
            if ref and stats.get("digest"):
                compressed = compressed.replace(f"#{stats['digest']}", ref)
            elif not ref and not stats.get("lossless_tier0"):
                compressed = combined
        sys.stdout.write(compressed)
        if not compressed.endswith("\n"):
            sys.stdout.write("\n")
        return proc.returncode
    if ns.cmd == "map":
        try:
            from .codemap import build_codebase_map
        except ImportError:
            from codemap import build_codebase_map  # type: ignore
        focus = getattr(ns, "focus", None)
        if focus and not (Path(ns.path) / focus.replace("\\", "/")).is_file():
            sys.stderr.write(f"Warning: --focus file not found: {focus} (map generated unfocused)\n")
        out = build_codebase_map(root=ns.path, budget_chars=ns.budget, max_files=ns.max_files, focus_file=focus)
        if out:
            sys.stdout.write(out + "\n")
        return 0
    if ns.cmd == "mcp-proxy":
        try:
            from .mcp_proxy import run_mcp_proxy
        except ImportError:
            from mcp_proxy import run_mcp_proxy  # type: ignore
        cmd_args = list(ns.command)
        if cmd_args and cmd_args[0] == "--":
            cmd_args = cmd_args[1:]
        if not cmd_args:
            sys.stderr.write("Error: no command provided to mcp-proxy. Usage: trimtok mcp-proxy [options] -- <command...>\n")
            return 1
        allow_set = {t.strip() for t in ns.allow.split(",") if t.strip()} if ns.allow else None
        deny_set = {t.strip() for t in ns.deny.split(",") if t.strip()} if ns.deny else None
        return run_mcp_proxy(
            cmd_args,
            allow_tools=allow_set,
            deny_tools=deny_set,
            instructions=ns.instructions,
            kv_lock=ns.kv_lock,
        )
    if ns.cmd == "kv-lock":
        try:
            from .kv_lock import stabilize_prompt
        except ImportError:
            from kv_lock import stabilize_prompt  # type: ignore
        raw = sys.stdin.read() if ns.input == "-" else Path(ns.input).read_text(encoding="utf-8", errors="replace")
        out = stabilize_prompt(raw)
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
