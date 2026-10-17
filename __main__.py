#!/usr/bin/env python3
"""Universal CLI for Trimtok v3.0.0.

Usage:
  trimtok install [--client <name>] [--dry-run] [--print]   # Auto-register MCP server in all detected agent CLIs/IDEs
  trimtok uninstall [--client <name>] [--dry-run]           # Remove MCP server registration
  trimtok clients                                           # List all supported agent clients and registration status
  trimtok mcp                                               # Start the stdio MCP server
  trimtok status | usage | purge                            # Inspect savings, usage stats, or purge CCR cache
  trimtok retrieve <ref> [errors|diff|summary|tail]         # Retrieve original or section from CCR v2
  trimtok compress [--cmd <cmd>] [--tool <name>] [file|-]   # Compress terminal or tool text from file or stdin
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from . import ccr, ccr_v2
    from .core import VERSION, compress_terminal
    from .dedup import compress_tool_result
    from .__init__ import _handle_retrieve, _handle_ts_command
    from .mcp_install import ServerSpec, add_install_arguments, run_clients, run_install
    from . import mcp_server
except ImportError:
    import ccr  # type: ignore
    import ccr_v2  # type: ignore
    from core import VERSION, compress_terminal  # type: ignore
    from dedup import compress_tool_result  # type: ignore
    from __init__ import _handle_retrieve, _handle_ts_command  # type: ignore
    from mcp_install import ServerSpec, add_install_arguments, run_clients, run_install  # type: ignore
    import mcp_server  # type: ignore


def _server_spec() -> ServerSpec:
    # Prefer `python -m trimtok.mcp_server` when installed as a package; fall back to script path
    script_path = str(Path(_ROOT) / "mcp_server.py")
    try:
        import importlib.util
        if importlib.util.find_spec("trimtok.mcp_server") is not None:
            return ServerSpec(name="trimtok", module="trimtok.mcp_server")
    except Exception:
        pass
    spec = ServerSpec(name="trimtok", module=script_path)
    return spec


def _sync_antigravity_schema_cache() -> None:
    """Populate ~/.gemini/antigravity-cli/mcp/trimtok/*.json if Antigravity CLI is present."""
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
    except Exception:
        pass


def _sync_global_skill() -> None:
    """Install SKILL.md into ~/.agents/skills/trimtok and ~/.gemini/config/skills/trimtok if present."""
    skill_md = (
        "---\n"
        "name: trimtok\n"
        "description: >-\n"
        "  Deterministic context compressor (Trimtok v3.0.0) for terminal outputs, tool results,\n"
        "  deep tracebacks, unified git diffs, and JSON payloads (TOON v2), backed by a two-tier\n"
        "  SQLite WAL Content-Controlled Retrieval (CCR) cache with section-based retrieval.\n"
        "---\n\n"
        "# Trimtok v3.0.0 — Context Compression & Section Retrieval\n\n"
        "Use `trimtok_compress_terminal`, `trimtok_compress_tool`, `trimtok_retrieve`, and `trimtok_status` "
        "to compress noisy outputs and retrieve exact sections (`errors`, `diff`, `summary`, `tail`).\n"
    )
    for root in (
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

    p_inst = sub.add_parser("install", help="Auto-register Trimtok MCP server in all detected agent CLIs/IDEs")
    add_install_arguments(p_inst)

    p_uninst = sub.add_parser("uninstall", help="Remove Trimtok MCP server registration from agent CLIs/IDEs")
    add_install_arguments(p_uninst)

    sub.add_parser("clients", help="List supported agent clients and Trimtok registration status")
    sub.add_parser("mcp", help="Run the stdio MCP server (JSON-RPC 2.0)")
    sub.add_parser("status", help="Show compression savings and CCR v2 cache status")
    sub.add_parser("usage", help="Show detailed hook/tool usage breakdown")
    sub.add_parser("purge", help="Purge cached CCR originals")

    p_ret = sub.add_parser("retrieve", help="Retrieve original content or a targeted section from CCR v2")
    p_ret.add_argument("ref", help="CCR reference (#c1 or 12-hex digest)")
    p_ret.add_argument("section", nargs="?", choices=["errors", "diff", "summary", "tail"], help="Optional section")
    p_ret.add_argument("--grep", help="Optional safe case-insensitive regex filter")
    p_ret.add_argument("--offset", type=int, help="1-indexed start line")
    p_ret.add_argument("--limit", type=int, help="Maximum lines to return")

    p_comp = sub.add_parser("compress", help="Compress terminal or tool text from a file or stdin")
    p_comp.add_argument("input", nargs="?", default="-", help="File path or '-' for stdin")
    p_comp.add_argument("--command", default="", help="Shell command name (for terminal mode)")
    p_comp.add_argument("--tool", default="", help="Tool name (switches to tool/TOON mode)")
    p_comp.add_argument("--min-chars", type=int, default=2048, help="Minimum char threshold (default: 2048)")

    ns = parser.parse_args(args_list)

    if ns.cmd in (None, "status"):
        print(_handle_ts_command("status"))
        print(f"Data directory: {ccr.default_dir()}")
        return 0
    if ns.cmd == "usage":
        print(_handle_ts_command("usage"))
        return 0
    if ns.cmd == "purge":
        print(_handle_ts_command("purge"))
        return 0
    if ns.cmd == "mcp":
        return mcp_server.main()
    if ns.cmd == "clients":
        return run_clients(_server_spec())
    if ns.cmd == "install":
        rc = run_install(_server_spec(), ns, remove=False)
        if not getattr(ns, "dry_run", False) and not getattr(ns, "print_only", False):
            _sync_antigravity_schema_cache()
            _sync_global_skill()
        return rc
    if ns.cmd == "uninstall":
        return run_install(_server_spec(), ns, remove=True)
    if ns.cmd == "retrieve":
        q: dict = {"hash": ns.ref}
        if ns.section:
            q["section"] = ns.section
        if ns.grep:
            q["grep"] = ns.grep
        if ns.offset is not None:
            q["offset"] = ns.offset
        if ns.limit is not None:
            q["limit"] = ns.limit
        print(_handle_retrieve(q))
        return 0
    if ns.cmd == "compress":
        raw = sys.stdin.read() if ns.input == "-" else Path(ns.input).read_text(encoding="utf-8", errors="replace")
        if ns.tool:
            compressed, stats = compress_tool_result(ns.tool, raw, min_chars=ns.min_chars)
        else:
            compressed, stats = compress_terminal(ns.command, raw, min_chars=ns.min_chars)
        if stats.get("gate") == "ok":
            ref = ccr_v2.put(raw)
            if ref and stats.get("digest"):
                compressed = compressed.replace(f"#{stats['digest']}", ref)
        sys.stdout.write(compressed)
        if not compressed.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
