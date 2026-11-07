"""Bounded JSON-RPC Stdio Proxy for MCP Servers.

Filters tool definitions from `tools/list` responses to prune system prompt bloat
and injects lightweight instructions into `initialize` responses.
Pure Python standard library (cross-platform, fail-open).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from typing import Set

HEADER_SEP_CRLF = b"\r\n\r\n"
HEADER_SEP_LF = b"\n\n"
CONTENT_LENGTH_RE = re.compile(rb"(?i)\bcontent-length:\s*(\d+)")


def _split_headers_and_body(raw_bytes: bytes) -> tuple[bool, bytes, bytes]:
    """Detect LSP-style Content-Length framing and split headers from body."""
    sep = HEADER_SEP_CRLF if HEADER_SEP_CRLF in raw_bytes else (HEADER_SEP_LF if HEADER_SEP_LF in raw_bytes else None)
    if sep is None:
        return False, b"", raw_bytes
    hdr_part, _, body = raw_bytes.partition(sep)
    if not CONTENT_LENGTH_RE.search(hdr_part):
        return False, b"", raw_bytes
    return True, hdr_part, body


def filter_mcp_message(
    raw_bytes: bytes,
    allow_tools: Set[str] | None = None,
    deny_tools: Set[str] | None = None,
    instructions: str | None = None,
    kv_lock: bool = False,
) -> bytes:
    """Filter tools in tools/list response or inject instructions in initialize response."""
    if not raw_bytes:
        return raw_bytes

    has_header, hdr_part, body_bytes = _split_headers_and_body(raw_bytes)
    trailing_newline = raw_bytes.endswith(b"\n") and not has_header

    # Strip UTF-8 BOM if present
    if body_bytes.startswith(b"\xef\xbb\xbf"):
        body_bytes = body_bytes[3:]

    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        # Fail-open guarantee: pass raw bytes untouched on parse failure
        return raw_bytes

    if not isinstance(data, dict):
        return raw_bytes

    modified = False
    result = data.get("result")
    if isinstance(result, dict):
        # 1. Filter tools in tools/list response
        if "tools" in result and isinstance(result["tools"], list):
            original_tools = result["tools"]
            filtered_tools = []
            for t in original_tools:
                if not isinstance(t, dict):
                    continue
                name = t.get("name")
                if allow_tools is not None and name not in allow_tools:
                    continue
                if deny_tools is not None and name in deny_tools:
                    continue
                filtered_tools.append(t)

            if len(filtered_tools) != len(original_tools):
                result["tools"] = filtered_tools
                modified = True

        # 2. Inject or override instructions in initialize response
        if instructions and ("protocolVersion" in result or "serverInfo" in result):
            result["instructions"] = instructions
            modified = True

        # 3. Stabilize instructions for KV-cache alignment if enabled
        if kv_lock and "instructions" in result and isinstance(result["instructions"], str):
            try:
                from .kv_lock import stabilize_prompt
            except ImportError:
                from kv_lock import stabilize_prompt  # type: ignore
            stabilized = stabilize_prompt(result["instructions"])
            if stabilized != result["instructions"]:
                result["instructions"] = stabilized
                modified = True

    if not modified:
        return raw_bytes

    new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if has_header:
        # Update Content-Length in existing header block
        new_hdr = CONTENT_LENGTH_RE.sub(
            f"Content-Length: {len(new_body)}".encode("ascii"),
            hdr_part,
            count=1,
        )
        return new_hdr + b"\r\n\r\n" + new_body
    if trailing_newline:
        return new_body + b"\n"
    return new_body


def _forward_stdin(child_stdin):
    """Forward parent stdin to child stdin."""
    try:
        src = sys.stdin.buffer
        while True:
            chunk = src.read(4096)
            if not chunk:
                break
            child_stdin.write(chunk)
            child_stdin.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            child_stdin.close()
        except Exception:
            pass


def _read_exact(stream, n: int) -> bytes:
    """Read exactly n bytes from stream, or until EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def run_mcp_proxy(
    command: list[str],
    allow_tools: Set[str] | None = None,
    deny_tools: Set[str] | None = None,
    instructions: str | None = None,
    kv_lock: bool = False,
) -> int:
    """Run MCP server subprocess and proxy stdio with JSON-RPC filtering."""
    if not command:
        sys.stderr.write("Error: no command provided to mcp-proxy\n")
        return 1

    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            bufsize=0,
        )
    except Exception as exc:
        sys.stderr.write(f"Error: failed to spawn {command[0]}: {exc}\n")
        return 1

    # Thread to pipe stdin to child
    if proc.stdin:
        in_thread = threading.Thread(target=_forward_stdin, args=(proc.stdin,), daemon=True)
        in_thread.start()

    # Read from child stdout, filter frames, and write to parent stdout
    child_stdout = proc.stdout
    if child_stdout is None:
        return proc.wait()
    parent_stdout = sys.stdout.buffer

    try:
        while True:
            line = child_stdout.readline()
            if not line:
                break

            # Handle Content-Length header framing
            if line.lower().startswith(b"content-length:"):
                # Read until empty line delimiter
                header_lines = [line]
                while True:
                    hdr = child_stdout.readline()
                    if not hdr or hdr in (b"\r\n", b"\n"):
                        break
                    header_lines.append(hdr)

                # Parse length
                m = re.search(r"\d+", line.decode("ascii", errors="ignore"))
                length = int(m.group(0)) if m else 0
                body = _read_exact(child_stdout, length) if length > 0 else b""
                raw_frame = b"".join(header_lines) + b"\r\n" + body
                filtered = filter_mcp_message(raw_frame, allow_tools, deny_tools, instructions, kv_lock=kv_lock)
                parent_stdout.write(filtered)
                parent_stdout.flush()
            else:
                # Newline-delimited JSON-RPC
                filtered = filter_mcp_message(line, allow_tools, deny_tools, instructions, kv_lock=kv_lock)
                parent_stdout.write(filtered)
                parent_stdout.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    return proc.wait()
