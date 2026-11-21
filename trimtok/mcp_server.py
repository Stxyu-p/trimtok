#!/usr/bin/env python3
"""Minimal stdio MCP server exposing Trimtok to any MCP-capable agent host.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout. Tools:
  trimtok_compress_terminal — compress terminal/shell output (windowing, diff, traceback, delta)
  trimtok_compress_tool     — compress tool/search/JSON output (TOON v2, scoped search, span fold, AST code skeleton)
  trimtok_retrieve          — retrieve original content or targeted sections (errors/diff/summary/tail)
  trimtok_exec              — execute bounded Python over a stored CCR payload without loading it into context
  trimtok_status            — inspect token savings, usage, doctor diagnostics, gain/discover, or bounded purge
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

# Optional msgspec acceleration for MCP stdio protocol
_MSGSPEC_AVAILABLE = False
try:
    import msgspec
    _MSGSPEC_AVAILABLE = True
except Exception:
    pass

try:
    from . import (
        EXEC_SCHEMA, RETRIEVE_SCHEMA, _audit, _handle_retrieve, _handle_ts_command,
        _storage_failure_stats, _terminal_shape, _tool_shape,
        _usage, _usage_exec, _parse_ref, ccr, policy, report,
    )
    from .core import VERSION, compress_terminal
    from .dedup import compress_tool_result
    from .signals import Deadline
except ImportError:
    import ccr
    import policy  # type: ignore
    import report  # type: ignore
    from core import VERSION, compress_terminal
    from dedup import compress_tool_result
    from signals import Deadline  # type: ignore
    from __init__ import (
        EXEC_SCHEMA, RETRIEVE_SCHEMA, _audit, _handle_retrieve, _handle_ts_command,
        _storage_failure_stats, _terminal_shape, _tool_shape,
        _usage, _usage_exec, _parse_ref,
    )

SERVER_INFO = {"name": "trimtok", "version": VERSION}
FALLBACK_PROTOCOL = "2024-11-05"
SERVER_INSTRUCTIONS = (
    f"Trimtok v{VERSION} — Deterministic Context Compressor & Lossless CCR Cache.\n\n"
    "## Mandatory Agent Habit (When to Use Trimtok)\n"
    "1. **Noisy Terminal / Test / Build Logs (>2 KB):** Call `trimtok_compress_terminal` (pass `output` or `file_path`) "
    "to compress repetitive logs while preserving 100% of errors, tracebacks, and unified git diffs.\n"
    "2. **Large Tool Outputs / Source Files / JSON Payloads (>2 KB):** Call `trimtok_compress_tool` (pass `result` or `file_path`) "
    "to apply lossless TOON v2 tabular JSON encoding, scoped search grouping, or AST code skeletonization without flooding your context window.\n"
    "3. **Lossless Section Retrieval:** Every compressed response stores the raw original in the SQLite WAL CCR cache and returns a `ref` (`#cN` or hex digest). "
    "Call `trimtok_retrieve` with `hash` + optional `section` (`errors`, `diff`, `summary`, `tail`), `grep`, or `offset`/`limit` whenever you need exact omitted lines.\n"
    "4. **Aggregate Questions Over a `ref`:** When the answer is a count, a filter, a top-N or a yes/no over a large payload, do NOT retrieve the text — "
    "call `trimtok_exec` with the `hash` and a short Python snippet. The payload path arrives in `$TRIMTOK_PAYLOAD`, and only stdout enters your context. "
    "Measured: the same 627 KB JSON question cost 224,523 bytes as a compressed table and 155 bytes this way."
)

TOOLS = [
    {
        "name": "trimtok_compress_terminal",
        "description": (
            "Compress noisy terminal or shell output deterministically while preserving 100% "
            "of errors, tracebacks, and git diff changes. Pass `output` directly or `file_path` to compress a log file on disk. "
            "Stores the raw original in CCR so any omitted lines can be retrieved via trimtok_retrieve."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command that produced the output"},
                "output": {"type": "string", "description": "Raw terminal stdout/stderr text (optional if file_path is set)"},
                "file_path": {"type": "string", "description": "Optional path to a log/output file on disk to read and compress directly"},
                "session_id": {"type": "string", "description": "Optional session identifier for cross-turn delta"},
                "delta_enabled": {"type": "boolean", "description": "Enable cross-turn command delta (default true)"},
                "min_chars": {"type": "integer", "description": "Minimum character threshold (default 2048)"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "trimtok_compress_tool",
        "description": (
            "Compress structured tool results or large files on disk (JSON arrays/objects via lossless TOON v2, "
            "multi-file grep/search hits via scoped search grouping, AST code skeletonization, and cross-turn repeated spans). "
            "Pass `result` directly or `file_path` to skeletonize/compress a file on disk without loading raw text into context. "
            "Stores the raw original in CCR for lossless retrieval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool that produced the result"},
                "result": {"type": "string", "description": "Raw tool output text or JSON string (optional if file_path is set)"},
                "file_path": {"type": "string", "description": "Optional path to a source/JSON/data file on disk to read and compress directly"},
                "session_id": {"type": "string", "description": "Optional session identifier for cross-turn folding"},
                "toon_enabled": {"type": "boolean", "description": "Enable TOON v2 tabular JSON encoding (default true)"},
                "min_chars": {"type": "integer", "description": "Minimum character threshold (default 2048)"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "trimtok_retrieve",
        "description": RETRIEVE_SCHEMA["description"],
        "inputSchema": {
            **RETRIEVE_SCHEMA["parameters"],
            "additionalProperties": False,
        },
    },
    {
        "name": "trimtok_exec",
        "description": EXEC_SCHEMA["description"],
        "inputSchema": {
            **EXEC_SCHEMA["parameters"],
            "additionalProperties": False,
        },
    },
    {
        "name": "trimtok_status",
        "description": "Inspect Trimtok compression stats, usage, doctor health checks, gain/discover reports, or bounded CCR purge.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "usage", "doctor", "gain", "discover", "purge"],
                    "description": "Action to run (default: status)",
                },
                "days": {
                    "type": "integer",
                    "description": "For action='gain': restrict report to last N days (0 = all)",
                },
                "older_than_hours": {
                    "type": "number",
                    "description": "For action='purge': delete entries older than N hours (0 = all unless max_mb set)",
                },
                "max_mb": {
                    "type": "number",
                    "description": "For action='purge': evict oldest unpinned entries until store <= N MB",
                },
                "scan_path": {
                    "type": "string",
                    "description": "For action='doctor': optional directory/file path to scan for persisted stubs",
                },
            },
            "additionalProperties": False,
        },
    },
]


class MethodNotFound(Exception):
    pass


_MAX_FILE_BYTES = 50 * 1024 * 1024


def _read_file_arg(file_path: Any) -> tuple[str, str]:
    if not isinstance(file_path, str) or not file_path.strip() or "\x00" in file_path:
        raise ValueError("file_path must be a non-empty valid path string")
    resolved = os.path.abspath(os.path.expanduser(file_path.strip()))
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"file not found: {resolved}")
    if os.path.getsize(resolved) > _MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {_MAX_FILE_BYTES // (1024 * 1024)} MB limit: {resolved}")
    with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(), resolved


def _usage_row(hook: str, name: str, stats: dict[str, Any], raw_len: int = 0) -> dict[str, Any]:
    """Log MCP traffic in the schema the savings summaries read.

    The summaries aggregate `before`/`after`/`saved`; logging `before_chars`
    from the stats dict made every MCP event invisible to `usage`/`gain`.
    """
    key = "cmd" if hook == "terminal" else "tool"
    before = int(stats.get("before_chars") or raw_len or 0)
    after_value = stats.get("after_chars")
    after = int(after_value) if stats.get("gate") == "ok" and after_value is not None else before
    shape = stats.get("kind") or (_terminal_shape(stats) if hook == "terminal" else _tool_shape(stats))
    row: dict[str, Any] = {
        "hook": hook,
        "surface": "mcp",
        key: name,
        "kind": stats.get("kind"),
        "shape": shape,
        "gate": stats.get("gate"),
        "before": before,
        "after": after,
        "saved": max(0, before - after),
        "hash": str(stats.get("digest") or ""),
        "reason": stats.get("reason"),
        "toon_saved": int(stats.get("toon_saved") or 0),
        "toon_used": bool(stats.get("toon_used", False)),
        "intra_template_saved": int(stats.get("intra_template_saved") or 0),
        "ms": round(float(stats.get("ms", 0.0)), 2),
    }
    return row


def _tool_compress_terminal(args: dict[str, Any]) -> dict[str, Any]:
    output = args.get("output")
    file_path = args.get("file_path")
    source_file = None
    if output is None and file_path is not None:
        output, source_file = _read_file_arg(file_path)
    if not isinstance(output, str):
        raise ValueError("output or file_path must be provided")
    command = str(args.get("command") or (f"cat {os.path.basename(source_file)}" if source_file else ""))
    session_id = str(args.get("session_id") or "mcp")
    delta_enabled = bool(args.get("delta_enabled", True))
    try:
        min_chars = max(128, min(1_000_000, int(args.get("min_chars", 2048))))
    except (TypeError, ValueError):
        min_chars = 2048
    if source_file:
        pol = policy.classify(output, tool="terminal", path=source_file)
        if pol.verbatim:
            stats = {
                "gate": "passthrough_policy",
                "reason": pol.reason,
                "before_chars": len(output),
                "after_chars": len(output),
                "saved_chars": 0,
                "ms": 0.0,
            }
            _usage(_usage_row("terminal", command[:120], stats, raw_len=len(output)))
            return {"compressed": output, "ref": "", "stats": stats, "source_file": source_file}
    compressed, stats = compress_terminal(
        command,
        output,
        min_chars=min_chars,
        session_id=session_id,
        delta_enabled=delta_enabled,
        deadline=Deadline(200.0),
    )
    ref = ""
    if stats.get("gate") == "ok":
        if stats.get("lossless_tier0"):
            _audit({"hook": "mcp_terminal", "cmd": command[:120], **stats})
        else:
            try:
                ref = ccr.put(output)
            except Exception:
                ref = ""
            if ref and stats.get("digest"):
                compressed = compressed.replace(f"#{stats['digest']}", ref)
                _audit({"hook": "mcp_terminal", "cmd": command[:120], **stats, "hash": ref})
            elif not ref:
                compressed = output
                stats = _storage_failure_stats(stats, output)
            else:
                _audit({"hook": "mcp_terminal", "cmd": command[:120], **stats})
    _usage(_usage_row("terminal", command[:120], stats, raw_len=len(output)))
    resp: dict[str, Any] = {
        "compressed": compressed,
        "ref": ref,
        "stats": stats,
    }
    if source_file:
        resp["source_file"] = source_file
    return resp


def _tool_compress_tool(args: dict[str, Any]) -> dict[str, Any]:
    result = args.get("result")
    file_path = args.get("file_path")
    source_file = None
    if result is None and file_path is not None:
        result, source_file = _read_file_arg(file_path)
    if not isinstance(result, str):
        raise ValueError("result or file_path must be provided")
    # A file read must go through the read-file lane, otherwise the lockfile
    # fast-gate and the secret/security path policy never run.
    default_tool = "read_file" if source_file else "tool"
    tool_name = str(args.get("tool_name") or default_tool)
    session_id = str(args.get("session_id") or "mcp")
    toon_enabled = bool(args.get("toon_enabled", True))
    try:
        min_chars = max(128, min(1_000_000, int(args.get("min_chars", 2048))))
    except (TypeError, ValueError):
        min_chars = 2048
    compressed, stats = compress_tool_result(
        tool_name,
        result,
        args={"path": source_file} if source_file else None,
        session_id=session_id,
        min_chars=min_chars,
        toon_enabled=toon_enabled,
        deadline=Deadline(200.0),
    )
    ref = ""
    if stats.get("gate") == "ok":
        if stats.get("lossless_tier0"):
            _audit({"hook": "mcp_tool", "tool": tool_name[:120], **stats})
        else:
            try:
                ref = ccr.put(result)
            except Exception:
                ref = ""
            if ref and stats.get("digest"):
                compressed = compressed.replace(f"#{stats['digest']}", ref)
                _audit({"hook": "mcp_tool", "tool": tool_name[:120], **stats, "hash": ref})
            elif not ref:
                compressed = result
                stats = _storage_failure_stats(stats, result)
            else:
                _audit({"hook": "mcp_tool", "tool": tool_name[:120], **stats})
    _usage(_usage_row("tool", tool_name[:120], stats, raw_len=len(result)))
    resp: dict[str, Any] = {
        "compressed": compressed,
        "ref": ref,
        "stats": stats,
    }
    if source_file:
        resp["source_file"] = source_file
    return resp


def _tool_retrieve(args: dict[str, Any]) -> dict[str, Any]:
    raw_json = _handle_retrieve(args)
    try:
        return json.loads(raw_json)
    except Exception:
        return {"result": raw_json}


def _tool_exec(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from .exec_lane import run as _exec_run
    except ImportError:
        from exec_lane import run as _exec_run  # type: ignore
    ref = _parse_ref(args.get("hash"))
    if not ref:
        return {"ok": False, "error": "hash is required (e.g. '#c1')", "output": "hash is required", "exit_code": 2}
    try:
        raw_t = float(args.get("timeout") if args.get("timeout") is not None else 10.0)
        if not math.isfinite(raw_t):
            raw_t = 10.0
    except (TypeError, ValueError):
        raw_t = 10.0
    timeout = max(0.1, min(30.0, raw_t))
    res = _exec_run(
        ref,
        str(args.get("code") or ""),
        timeout=timeout,
    )
    _usage_exec(args, res)
    return res


def _tool_status(args: dict[str, Any]) -> dict[str, Any]:
    raw_action = args.get("action")
    action = str(raw_action if raw_action is not None else "status").strip().lower() or "status"
    if action not in ("status", "usage", "doctor", "gain", "discover", "purge"):
        return {"version": VERSION, "error": f"unknown action: {action!r}"}
    if action == "doctor":
        scan_path = str(args.get("scan_path") or "").strip()
        scan_list = [scan_path] if scan_path else None
        diag = report.run_doctor(scan_paths=scan_list)
        return {"version": VERSION, "action": "doctor", **diag}
    if action == "gain":
        try:
            days = max(0, int(args.get("days") or 0))
        except (TypeError, ValueError):
            days = 0
        text = _handle_ts_command(f"gain {days}" if days else "gain")
        return {"version": VERSION, "action": "gain", "summary": text, "ccr": ccr.stats()}
    if action == "purge":
        raw_hours = args.get("older_than_hours")
        raw_max_mb = args.get("max_mb")
        try:
            hours = 0.0 if raw_hours is None else float(raw_hours)
            max_mb = 0.0 if raw_max_mb is None else float(raw_max_mb)
        except (TypeError, ValueError):
            return {"version": VERSION, "action": "purge", "error": "older_than_hours and max_mb must be numbers >= 0"}
        if not math.isfinite(hours) or not math.isfinite(max_mb) or hours < 0.0 or max_mb < 0.0:
            return {"version": VERSION, "action": "purge", "error": "older_than_hours and max_mb must be finite numbers >= 0"}
        before = ccr.stats()
        purged = 0
        evicted = 0
        if hours > 0:
            purged = ccr.purge(before_seconds=hours * 3600.0)
        elif max_mb <= 0:
            purged = ccr.purge()
        if max_mb > 0:
            evicted = ccr.evict_to_size(int(max_mb * 1024 * 1024))
        after = ccr.stats()
        return {
            "version": VERSION,
            "action": "purge",
            "purged": purged,
            "evicted": evicted,
            "summary": f"trimtok: purged {purged}, evicted {evicted} ({before.get('entries', 0)} -> {after.get('entries', 0)} entries).",
            "ccr": after,
        }
    text = _handle_ts_command(action)
    ccr_info = ccr.stats()
    return {
        "version": VERSION,
        "action": action,
        "summary": text,
        "ccr": ccr_info,
    }


_HANDLERS = {
    "trimtok_compress_terminal": _tool_compress_terminal,
    "trimtok_compress_tool": _tool_compress_tool,
    "trimtok_retrieve": _tool_retrieve,
    "trimtok_exec": _tool_exec,
    "trimtok_status": _tool_status,
}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    parameters = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method == "initialize":
        requested = parameters.get("protocolVersion")
        protocol = requested if isinstance(requested, str) and requested else FALLBACK_PROTOCOL
        return {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": SERVER_INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if isinstance(method, str) and method.startswith("notifications/"):
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = str(parameters.get("name") or "")
        handler = _HANDLERS.get(name)
        if handler is None:
            raise MethodNotFound(f"unknown tool: {name}")
        arguments = parameters.get("arguments") if isinstance(parameters.get("arguments"), dict) else {}
        try:
            result = handler(arguments)
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
            ],
            # A handler that returns {"error": ...} or {"ok": False} did not succeed.
            "isError": isinstance(result, dict) and ("error" in result or result.get("ok") is False),
        }
    raise MethodNotFound(str(method))


def _write(payload: dict[str, Any]) -> None:
    if _MSGSPEC_AVAILABLE:
        try:
            raw = msgspec.json.encode(payload)
            sys.stdout.buffer.write(raw + b"\n")
            sys.stdout.buffer.flush()
            return
        except Exception:
            pass
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                if _MSGSPEC_AVAILABLE:
                    try:
                        request = msgspec.json.decode(line.encode("utf-8"))
                    except Exception:
                        request = json.loads(line)
                else:
                    request = json.loads(line)
            except ValueError:
                _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            if not isinstance(request, dict) or not isinstance(request.get("method"), str):
                req_id = request.get("id") if isinstance(request, dict) else None
                _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "invalid request"}})
                continue
            has_id = "id" in request
            request_id = request.get("id")
            try:
                result = handle(request)
            except MethodNotFound as exc:
                if has_id:
                    _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": str(exc)}})
                continue
            except Exception as exc:
                if has_id:
                    _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": f"internal error: {exc}"}})
                continue
            if has_id:
                _write({"jsonrpc": "2.0", "id": request_id, "result": result})
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
