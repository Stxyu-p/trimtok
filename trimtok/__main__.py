#!/usr/bin/env python3
"""Universal CLI for Trimtok.

Usage:
  trimtok install [--client <name>] [--dry-run] [--print]   # Auto-register MCP server in all detected agent CLIs/IDEs
  trimtok uninstall [--client <name>] [--dry-run]           # Remove MCP server registration
  trimtok clients                                           # List all supported agent clients and registration status
  trimtok mcp                                               # Start the stdio MCP server
  trimtok status | usage | doctor | purge                   # Inspect savings, usage, health, or purge CCR cache
  trimtok guard-check [paths...] [--staged] [--json]        # Detect persisted [tt:...] stub markers
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
        "Use `trimtok_compress_terminal`, `trimtok_compress_tool`, `trimtok_retrieve`, `trimtok_exec`, and `trimtok_status` "
        "(actions: `status`, `usage`, `doctor`, `gain`, `discover`, `purge`) "
        "to compress noisy outputs or large files on disk (`file_path`) and retrieve exact sections (`errors`, `diff`, `summary`, `tail`).\n"
    )
    _ag_vault = Path.home() / ".agents" / "skills"
    _lg_vault = Path.home() / ".skill-proof" / "skills"
    vault_root = _ag_vault if _ag_vault.is_dir() else _lg_vault
    seen_roots: set[Path] = set()
    for root in (
        vault_root,
        _ag_vault,
        Path.home() / ".gemini" / "config" / "skills",
    ):
        try:
            if not root.is_dir():
                continue
            resolved_root = root.resolve()
            if resolved_root in seen_roots:
                continue
            seen_roots.add(resolved_root)
            d = root / "trimtok"
            d.mkdir(parents=True, exist_ok=True)
            target = d / "SKILL.md"
            # Refresh stub in vault only when missing or not already replaced by a richer guide.
            if not target.exists() or (
                root == vault_root and target.stat().st_size <= len(skill_md.encode("utf-8")) + 64
            ):
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
    # A negative bound used to fall through to the bare-purge branch and wipe the
    # store: `purge --older-than -1` deleted every cached original.
    if hours < 0 or max_mb < 0:
        sys.stderr.write("Error: --older-than and --max-mb must be >= 0\n")
        return 2

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


def _build_parser() -> argparse.ArgumentParser:
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
    p_doc = sub.add_parser("doctor", help="Run standalone CCR, telemetry, and optional stub integrity checks")
    p_doc.add_argument("--json", action="store_true", help="Output unified JSON health envelope")
    p_doc.add_argument("--scan-path", action="append", default=[], help="Optional file/directory to scan for persisted stubs")
    p_guard = sub.add_parser("guard-check", help="Scan files or staged git diff for persisted [tt:...] stubs")
    p_guard.add_argument("paths", nargs="*", help="Paths to scan (default: current directory)")
    p_guard.add_argument("--staged", action="store_true", help="Scan staged git diff additions instead of files")
    p_guard.add_argument("--json", action="store_true", help="Output JSON scan report")
    sub.add_parser("gain", help="Show detailed character savings gain report").add_argument("--days", type=int, default=0)
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

    p_exec = sub.add_parser("exec", help="Run code over a stored original (#ref) without loading it into context")
    p_exec.add_argument("ref", help="CCR reference (#c1 or 12-hex digest)")
    p_exec.add_argument("--code", help="Python (default) or shell code; read $TRIMTOK_PAYLOAD")
    p_exec.add_argument("--file", help="Read the code from a file instead of --code")
    p_exec.add_argument("--timeout", type=float, default=10.0)

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
    return parser


def _dispatch(ns: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if ns.cmd in (None, "status"):
        print(_handle_ts_command("status"))
        print(f"Data directory: {ccr.default_dir()}")
        return 0
    if ns.cmd == "usage":
        print(_handle_ts_command("usage"))
        return 0
    if ns.cmd == "doctor":
        try:
            from . import report
        except ImportError:
            import report  # type: ignore
        res = report.run_doctor(scan_paths=ns.scan_path or None)
        if ns.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"trimtok doctor (v{res['version']}): {res['status'].upper()}")
            for chk in res["checks"]:
                print(f"  [{chk['status'].upper()}] {chk['id']}: {chk['detail']}")
                if chk.get("fix_hint"):
                    print(f"       fix: {chk['fix_hint']}")
        return 1 if res["status"] == "fail" else 0
    if ns.cmd == "guard-check":
        try:
            from .guard import scan_paths
        except ImportError:
            from guard import scan_paths  # type: ignore
        res = scan_paths(ns.paths or ["."], staged=bool(ns.staged))
        if ns.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif res["clean"]:
            print(f"trimtok guard-check: clean ({res['files_scanned']} files scanned).")
        else:
            print(f"trimtok guard-check: FOUND {len(res['findings'])} persisted stub(s) in {res['corrupted_files']} file(s):")
            for f in res["findings"][:30]:
                print(f"  {f['file']}:{f['line']} [{f['kind']}] {f['marker']}")
        return 0 if res["clean"] else 1
    if ns.cmd == "gain":
        days = int(getattr(ns, "days", 0) or 0)
        if days < 0:
            sys.stderr.write("Error: --days must be >= 0\n")
            return 2
        print(_handle_ts_command(f"gain {days}" if days else "gain"))
        return 0
    if ns.cmd == "discover":
        print(_handle_ts_command("discover"))
        return 0
    if ns.cmd == "recall":
        h = str(getattr(ns, "hash", "") or "").strip()
        out = _handle_ts_command(f"retrieve {h}" if h else "retrieve")
        print(out)
        low = out.lower()
        return 1 if (not h or "not found" in low or "usage:" in low) else 0
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
            if "error" in res_obj:
                return 1
        except Exception:
            pass
        return 0
    if ns.cmd == "exec":
        try:
            from .exec_lane import run as _exec_run
        except ImportError:
            from exec_lane import run as _exec_run  # type: ignore
        code = ns.code or ""
        if ns.file:
            try:
                code = Path(ns.file).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                sys.stderr.write(f"Error: cannot read {ns.file}: {exc}\n")
                return 1
        res = _exec_run(ns.ref, code, timeout=ns.timeout)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    if ns.cmd == "search":
        if not ccr.fts_ready():
            sys.stderr.write("Error: FTS5 search is unavailable in this SQLite build.\n")
            return 2
        limit = max(1, min(100, int(getattr(ns, "limit", 5) or 5)))
        hits = ccr.search(ns.query, limit=limit)
        print(json.dumps({"query": ns.query, "count": len(hits), "hits": hits}, indent=2, ensure_ascii=False))
        return 0
    if ns.cmd == "compress":
        try:
            raw = sys.stdin.read() if ns.input == "-" else Path(ns.input).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            sys.stderr.write(f"Error: cannot read {ns.input}: {exc}\n")
            return 1
        if ns.tool:
            _src = {"path": ns.input} if ns.input != "-" else None
            compressed, stats = compress_tool_result(ns.tool, raw, args=_src, min_chars=ns.min_chars)
        else:
            compressed, stats = compress_terminal(ns.command, raw, min_chars=ns.min_chars)
        if stats.get("gate") == "ok" and not stats.get("lossless_tier0"):
            ref = ccr.put(raw)
            if ref and stats.get("digest"):
                compressed = compressed.replace(f"#{stats['digest']}", ref)
            elif not ref:
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
        try:
            proc = subprocess.run(
                cmd_args, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False
            )
        except FileNotFoundError:
            sys.stderr.write(f"Error: command not found: {cmd_args[0]}\n")
            return 127
        except PermissionError:
            sys.stderr.write(f"Error: permission denied: {cmd_args[0]}\n")
            return 126
        combined = (proc.stdout or "") + (proc.stderr or "")
        cmd_name = Path(cmd_args[0]).stem
        compressed, stats = compress_terminal(cmd_name, combined, min_chars=512, exit_code=proc.returncode)
        if stats.get("gate") == "ok" and not stats.get("lossless_tier0"):
            ref = ccr.put(combined)
            if ref and stats.get("digest"):
                compressed = compressed.replace(f"#{stats['digest']}", ref)
            elif not ref:
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
        try:
            raw = sys.stdin.read() if ns.input == "-" else Path(ns.input).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            sys.stderr.write(f"Error: cannot read {ns.input}: {exc}\n")
            return 1
        out = stabilize_prompt(raw)
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    ns = parser.parse_args(args_list)
    return _dispatch(ns, parser)


if __name__ == "__main__":
    sys.exit(main())
