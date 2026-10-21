#!/usr/bin/env python3
"""Minimal stdio MCP server exposing Trimtok v4.2.1 to any MCP-capable agent host.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout. Tools:
  trimtok_compress_terminal — compress terminal/shell output (windowing, diff, traceback, delta)
  trimtok_compress_tool     — compress tool/search/JSON output (TOON v2, scoped search, span fold, AST code skeleton)
  trimtok_retrieve          — retrieve original content or targeted sections (errors/diff/summary/tail)
  trimtok_status            — inspect token savings, CCR cache stats, or usage summary
"""

from __future__ import annotations

import json
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
    from . import ccr
    from .core import VERSION, compress_terminal
    from .dedup import compress_tool_result
    from .__init__ import RETRIEVE_SCHEMA, _audit, _handle_retrieve, _handle_ts_command, _usage
except ImportError:
    import ccr
    from core import VERSION, compress_terminal
    from dedup import compress_tool_result
    from __init__ import RETRIEVE_SCHEMA, _audit, _handle_retrieve, _handle_ts_command, _usage

SERVER_INFO = {"name": "trimtok", "version": VERSION}
FALLBACK_PROTOCOL = "2024-11-05"
SERVER_INSTRUCTIONS = (
    "Trimtok v4.2.2 — Deterministic Context Compressor & Lossless CCR Cache.\n\n"
    "## Mandatory Agent Habit (When to Use Trimtok)\n"
    "1. **Noisy Terminal / Test / Build Logs (>2 KB):** Call `trimtok_compress_terminal` (pass `output` or `file_path`) "
    "to compress repetitive logs while preserving 100% of errors, tracebacks, and unified git diffs.\n"
    "2. **Large Tool Outputs / Source Files / JSON Payloads (>2 KB):** Call `trimtok_compress_tool` (pass `result` or `file_path`) "
    "to apply lossless TOON v2 tabular JSON encoding, scoped search grouping, or AST code skeletonization without flooding your context window.\n"
    "3. **Lossless Section Retrieval:** Every compressed response stores the raw original in the SQLite WAL CCR cache and returns a `ref` (`#cN` or hex digest). "
    "Call `trimtok_retrieve` with `hash` + optional `section` (`errors`, `diff`, `summary`, `tail`), `grep`, or `offset`/`limit` whenever you need exact omitted lines."
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
        "name": "trimtok_status",
        "description": "Inspect Trimtok v4.2.1 compression stats, CCR cache size, or purge expired entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "usage", "purge"],
                    "description": "Action to run (default: status)",
                }
            },
            "additionalProperties": False,
        },
    },
]


class MethodNotFound(Exception):
    pass


def _read_file_arg(file_path: Any) -> tuple[str, str]:
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("file_path must be a non-empty string")
    resolved = os.path.abspath(os.path.expanduser(file_path.strip()))
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"file not found: {resolved}")
    with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(), resolved


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
    compressed, stats = compress_terminal(
        command,
        output,
        min_chars=min_chars,
        session_id=session_id,
        delta_enabled=delta_enabled,
    )
    ref = ""
    if stats.get("gate") == "ok":
        ref = ccr.put(output)
        if ref and stats.get("digest"):
            compressed = compressed.replace(f"#{stats['digest']}", ref)
        _audit({"hook": "mcp_terminal", "cmd": command[:120], **stats})
    _usage({"hook": "terminal", "cmd": command[:120], "shape": stats.get("kind", "mixed"), **stats})
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
    default_tool = f"Read:{os.path.basename(source_file)}" if source_file else "tool"
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
        session_id=session_id,
        min_chars=min_chars,
        toon_enabled=toon_enabled,
    )
    ref = ""
    if stats.get("gate") == "ok":
        ref = ccr.put(result)
        if ref and stats.get("digest"):
            compressed = compressed.replace(f"#{stats['digest']}", ref)
        _audit({"hook": "mcp_tool", "tool": tool_name[:120], **stats})
    _usage({"hook": "tool", "tool": tool_name[:120], "shape": stats.get("kind", "tool"), **stats})
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


def _tool_status(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "status").strip().lower()
    if action not in ("status", "usage", "purge"):
        action = "status"
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
            "isError": False,
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
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
        if not isinstance(request, dict):
            continue
        request_id = request.get("id")
        try:
            result = handle(request)
        except MethodNotFound as exc:
            if request_id is not None:
                _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": str(exc)}})
            continue
        except Exception as exc:
            if request_id is not None:
                _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": f"internal error: {exc}"}})
            continue
        if request_id is not None:
            _write({"jsonrpc": "2.0", "id": request_id, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
