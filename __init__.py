"""trimtok Hermes plugin adapter. Glue over core.py/ccr.py/dedup.py."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

try:
    from .core import VERSION, compress_terminal
    from .ccr import default_dir, get as ccr_get, purge as ccr_purge, put as ccr_put, stats as ccr_stats
    from .dedup import (
        clear_session, compress_tool_result, fold_spans, mask_old_turns,
        note_turn, remember,
    )
except ImportError:  # direct unit-test import
    from core import VERSION, compress_terminal
    from ccr import default_dir, get as ccr_get, purge as ccr_purge, put as ccr_put, stats as ccr_stats
    from dedup import (
        clear_session, compress_tool_result, fold_spans, mask_old_turns,
        note_turn, remember,
    )

logger = logging.getLogger(__name__)

_MAX_GREP_PATTERN_CHARS = 256
_MAX_GREP_INPUT_CHARS = 1_000_000
_MAX_GREP_MATCHES = 1000
_MAX_GREP_OUTPUT_CHARS = 100_000
_MAX_AUDIT_BYTES = 1_048_576
_AUDIT_LOCK = threading.Lock()

RETRIEVE_SCHEMA = {
    "name": "trimtok_retrieve",
    "description": (
        "Retrieve the uncompressed original content of a previously folded or windowed "
        "tool result by its content hash. Supports optional line pagination (offset/limit) "
        "and grep regex filtering to inspect specific details without flooding context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hash": {
                "type": "string",
                "description": "The content hash shown in the [tt:... retrieve:<hash>] marker.",
            },
            "offset": {
                "type": "integer",
                "description": "Optional 1-indexed line number to start reading from.",
            },
            "limit": {
                "type": "integer",
                "description": "Optional maximum number of lines to return.",
            },
            "grep": {
                "type": "string",
                "description": (
                    "Optional case-insensitive safe regex (no groups or multiple repetitions); "
                    "limited to 256 pattern characters, 1 MB input, 1000 matches, and 100 KB output."
                ),
            },
        },
        "required": ["hash"],
    },
}


def _audit_path() -> Path:
    return default_dir() / "audit.jsonl"


def _audit(entry: dict, ctx: Any = None) -> None:
    if ctx is not None and not _bool_cfg(ctx, "audit_enabled", True):
        return
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        if len(line.encode("utf-8")) > _MAX_AUDIT_BYTES:
            line = json.dumps({"event": "audit_entry_oversize"}) + "\n"
        with _AUDIT_LOCK:
            backup = path.with_name(path.name + ".1")
            if backup.exists() and backup.stat().st_size > _MAX_AUDIT_BYTES:
                backup.unlink(missing_ok=True)
            if path.exists() and path.stat().st_size > _MAX_AUDIT_BYTES:
                path.write_text(json.dumps({"event": "audit_history_truncated"}) + "\n", encoding="utf-8")
            if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > _MAX_AUDIT_BYTES:
                os.replace(path, backup)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def _int_cfg(ctx: Any, key: str, default: int, low: int, high: int) -> int:
    try:
        v = ctx.get_config(key, default)
        n = int(v)
        return max(low, min(high, n))
    except Exception:
        return default


def _list_cfg(ctx: Any, key: str) -> list[str]:
    try:
        value = ctx.get_config(key, [])
        if isinstance(value, list):
            return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    except Exception:
        pass
    return []


def _excluded_command(command: Any, ctx: Any) -> bool:
    normalized = " ".join(str(command or "").split()).casefold()
    if not normalized:
        return False
    for excluded in _list_cfg(ctx, "exclude_commands"):
        prefix = " ".join(excluded.split()).casefold()
        if normalized == prefix or normalized.startswith(prefix + " "):
            return True
    return False


def _excluded_tool(tool_name: Any, ctx: Any) -> bool:
    normalized = str(tool_name or "").strip().casefold()
    return bool(normalized) and any(
        normalized == excluded.casefold() for excluded in _list_cfg(ctx, "exclude_tools")
    )


def _compile_grep(source: str) -> re.Pattern[str]:
    """Compile a bounded regex subset without grouped/nested repetition."""
    if len(source) > _MAX_GREP_PATTERN_CHARS:
        raise ValueError(f"pattern exceeds {_MAX_GREP_PATTERN_CHARS} characters")

    repeats = 0
    in_class = False
    escaped = False
    last_was_repeat = False
    for char in source:
        if escaped:
            if char.isdigit():
                raise ValueError("backreferences are not supported in grep patterns")
            escaped = False
            last_was_repeat = False
            continue
        if char == "\\":
            escaped = True
            last_was_repeat = False
            continue
        if in_class:
            if char == "]":
                in_class = False
            continue
        if char == "[":
            in_class = True
            last_was_repeat = False
            continue
        if char in "(){}":
            raise ValueError("groups and counted repetition are not supported in grep patterns")
        if char in "*+?":
            if char == "?" and last_was_repeat:  # lazy suffix, not a second repeat
                last_was_repeat = False
                continue
            repeats += 1
            if repeats > 1:
                raise ValueError("only one repetition operator is supported in grep patterns")
            last_was_repeat = True
        else:
            last_was_repeat = False
    return re.compile(source, re.IGNORECASE)


def _on_transform(
    command: Any = None,
    output: Any = None,
    returncode: Any = 0,
    **kw: Any,
) -> str | None:
    """transform_terminal_output: return compressed string or None (passthrough)."""
    ctx = _on_transform._ctx  # type: ignore[attr-defined]
    if not _bool_cfg(ctx, "enabled", True):
        return None
    if not isinstance(output, str):
        return None
    if _excluded_command(command, ctx):
        return None
    min_chars = _int_cfg(ctx, "min_chars", 2048, 0, 10_000_000)
    new_output, stats = compress_terminal(
        str(command or ""),
        output,
        min_chars=min_chars,
        max_lines=_int_cfg(ctx, "max_lines", 120, 10, 10000),
    )
    if stats.get("gate") != "ok":
        return None
    digest = stats.get("digest", "")
    stored = ccr_put(
        output,
        max_entries=_int_cfg(ctx, "ccr_max_entries", 1000, 10, 100000),
        max_bytes=_int_cfg(ctx, "ccr_max_bytes", 50 * 1024 * 1024, 1024, 1_000_000_000),
        ttl_hours=_int_cfg(ctx, "ccr_ttl_hours", 24, 1, 24 * 30),
    )
    if not stored:
        return None
    if stored and stored != digest:
        new_output = new_output.replace(f"retrieve:{digest}", f"retrieve:{stored}", 1)
        digest = stored
    _audit({
        "tool": "terminal",
        "command": str(command or "")[:120],
        "kind": stats.get("kind"),
        "before_chars": stats.get("before_chars"),
        "after_chars": stats.get("after_chars"),
        "saved_chars": stats.get("saved_chars"),
        "gate": "ok",
        "hash": digest,
        "ms": round(float(stats.get("ms", 0.0)), 2),
    }, ctx=ctx)
    return new_output


def _bool_cfg(ctx: Any, key: str, default: bool) -> bool:
    try:
        v = ctx.get_config(key, default)
        return v if isinstance(v, bool) else default
    except Exception:
        return default


def _on_tool_result(
    tool_name: Any = None,
    args: Any = None,
    result: Any = None,
    session_id: Any = None,
    task_id: Any = None,
    turn_id: Any = None,
    **kw: Any,
) -> str | None:
    """transform_tool_result: return compressed string or None (passthrough)."""
    ctx = _on_tool_result._ctx  # type: ignore[attr-defined]
    if not _bool_cfg(ctx, "enabled", True):
        return None
    if not _bool_cfg(ctx, "phase2_enabled", True):
        return None
    if not isinstance(result, str):
        return None
    if _excluded_tool(tool_name, ctx):
        return None
    sid = str(session_id or task_id or "default")
    turn = str(turn_id or "")
    new_result, stats = compress_tool_result(
        str(tool_name or ""),
        result,
        session_id=sid,
        min_chars=_int_cfg(ctx, "min_chars", 2048, 0, 10_000_000),
        toon_enabled=_bool_cfg(ctx, "toon_enabled", False),
    )
    if stats.get("gate") != "ok":
        return None
    digest = stats.get("digest", "")
    stored = ccr_put(
        result,
        max_entries=_int_cfg(ctx, "ccr_max_entries", 1000, 10, 100000),
        max_bytes=_int_cfg(ctx, "ccr_max_bytes", 50 * 1024 * 1024, 1024, 1_000_000_000),
        ttl_hours=_int_cfg(ctx, "ccr_ttl_hours", 24, 1, 24 * 30),
    )
    if not stored:
        return None
    if stored and stored != digest:
        new_result = new_result.replace(f"retrieve:{digest}", f"retrieve:{stored}", 1)
        digest = stored
    note_turn(result, digest, session_id=sid, turn_id=turn)
    _audit({
        "tool": str(tool_name or "tool"),
        "kind": "tool_result",
        "before_chars": stats.get("before_chars"),
        "after_chars": stats.get("after_chars"),
        "saved_chars": stats.get("saved_chars"),
        "gate": "ok",
        "hash": digest,
        "ms": round(float(stats.get("ms", 0.0)), 2),
    }, ctx=ctx)
    return new_result


def _on_post_llm(
    session_id: Any = None,
    task_id: Any = None,
    turn_id: Any = None,
    **kw: Any,
) -> None:
    """post_llm_call: age fold index by turn clock (freshness mask)."""
    ctx = _on_post_llm._ctx  # type: ignore[attr-defined]
    if not _bool_cfg(ctx, "enabled", True) or not _bool_cfg(ctx, "mask_enabled", True):
        return None
    sid = str(session_id or task_id or "default")
    try:
        from .dedup import expire_old_turns
    except ImportError:
        from dedup import expire_old_turns  # type: ignore
    try:
        expired = expire_old_turns(
            sid,
            keep_turns=_int_cfg(ctx, "mask_keep_turns", 2, 0, 32),
        )
        if expired:
            _audit({"tool": "mask", "expired_turns": expired, "gate": "ok", "ms": 0.0}, ctx=ctx)
    except Exception:
        pass
    return None


def _handle_retrieve(args: dict, **kw: Any) -> str:
    digest = str((args or {}).get("hash") or "").strip().strip("<>").removeprefix("tt:").split(",")[0].strip()
    digest = "".join(c for c in digest if c in "0123456789abcdef")[:16]
    if not digest:
        return _err("hash is required (from a '[tt:... retrieve:<hash>]' marker)")
    ctx = getattr(_handle_retrieve, "_ctx", None)
    ttl_hours = _int_cfg(ctx, "ccr_ttl_hours", 24, 1, 24 * 30) if ctx is not None else 24
    content = ccr_get(digest, ttl_hours=ttl_hours)
    if content is None:
        return _err("Not found or expired (TTL passed). Re-run the original command instead.")

    grep = str((args or {}).get("grep") or "").strip()
    if grep:
        try:
            if len(content) > _MAX_GREP_INPUT_CHARS:
                return _err(
                    f"Content exceeds the {_MAX_GREP_INPUT_CHARS}-character grep limit; retrieve a smaller slice."
                )
            pattern = _compile_grep(grep)
            lines = content.splitlines()
            total_lines = len(lines)
            matched: list[str] = []
            output_chars = 0
            matches_count = 0
            truncated = False
            for i, line in enumerate(lines):
                if not pattern.search(line):
                    continue
                matches_count += 1
                rendered = f"{i + 1}|{line}"
                extra = len(rendered) + (1 if matched else 0)
                if len(matched) >= _MAX_GREP_MATCHES or output_chars + extra > _MAX_GREP_OUTPUT_CHARS:
                    truncated = True
                    continue
                matched.append(rendered)
                output_chars += extra
            return _ok({
                "matches": "\n".join(matched) if matched else "(no matches found)",
                "matches_count": matches_count,
                "total_lines": total_lines,
                "truncated": truncated,
            })
        except Exception as e:
            return _err(f"Invalid grep pattern: {e}")

    lines = content.splitlines()
    total_lines = len(lines)
    offset = (args or {}).get("offset")
    limit = (args or {}).get("limit")
    if offset is not None or limit is not None:
        start = max(0, int(offset or 1) - 1)
        cnt = int(limit) if limit is not None else 1000
        end = min(total_lines, start + max(1, cnt))
        sliced = [f"{i + 1}|{lines[i]}" for i in range(start, end)]
        return _ok({
            "lines": "\n".join(sliced),
            "offset": start + 1,
            "total_lines": total_lines,
            "returned_lines": len(sliced),
        })

    return _ok({"original_content": content, "original_chars": len(content), "total_lines": total_lines})


def _err(message: str) -> str:
    try:
        from tools.registry import tool_error  # type: ignore
        return tool_error(message)
    except Exception:
        return json.dumps({"error": message}, ensure_ascii=False)


def _ok(data: dict) -> str:
    try:
        from tools.registry import tool_result  # type: ignore
        return tool_result(data)
    except Exception:
        return json.dumps(data, ensure_ascii=False)


def _handle_ts_command(raw_args: str) -> str:
    arg = (raw_args or "").strip().lower()
    if arg in ("", "status"):
        try:
            path = _audit_path()
            backup = path.with_name(path.name + ".1")
            lines = []
            with _AUDIT_LOCK:
                for audit_file in (backup, path):
                    if audit_file.exists():
                        lines.extend(audit_file.read_text(encoding="utf-8").splitlines())
            saved = sum((json.loads(ln).get("saved_chars") or 0) for ln in lines if ln.strip().startswith("{"))
            ctx = getattr(_handle_ts_command, "_ctx", None)
            ttl_hours = _int_cfg(ctx, "ccr_ttl_hours", 24, 1, 24 * 30) if ctx is not None else 24
            c_stat = ccr_stats(ttl_hours=ttl_hours)
            return (
                f"trimtok v{VERSION}: {len(lines)} transforms logged (recent window), ~{saved:,} chars saved.\n"
                f"CCR Cache: {c_stat.get('entries', 0)} files (~{c_stat.get('bytes', 0) // 1024} KB)."
            )
        except Exception:
            return f"trimtok v{VERSION}: status unavailable."
    if arg == "stats":
        return _handle_ts_command("status")
    if arg == "purge":
        purged = ccr_purge()
        return f"trimtok v{VERSION}: purged {purged} cached original files."
    if arg.startswith("retrieve"):
        h = arg.partition(" ")[2].strip()
        return _handle_retrieve({"hash": h})
    return "Usage: /trimtok [status|stats|purge|retrieve <hash>]"


def _on_session_end(session_id: Any = None, **kw: Any) -> None:
    clear_session(str(session_id or ""))


def _on_session_start(session_id: Any = None, **kw: Any) -> None:
    clear_session(str(session_id or ""))


def register(ctx: Any) -> None:
    _on_transform._ctx = ctx  # type: ignore[attr-defined]
    _on_tool_result._ctx = ctx  # type: ignore[attr-defined]
    _on_post_llm._ctx = ctx  # type: ignore[attr-defined]
    _handle_retrieve._ctx = ctx  # type: ignore[attr-defined]
    _handle_ts_command._ctx = ctx  # type: ignore[attr-defined]
    ctx.register_hook("transform_terminal_output", _on_transform)
    ctx.register_hook("transform_tool_result", _on_tool_result)
    ctx.register_hook("post_llm_call", _on_post_llm)
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_tool(
        name="trimtok_retrieve",
        toolset="trimtok",
        schema=RETRIEVE_SCHEMA,
        handler=_handle_retrieve,
        emoji="\U0001f9f9",
    )
    if hasattr(ctx, "register_slash_command"):
        ctx.register_slash_command(
            name="trimtok",
            description="View trimtok token savings, status, or retrieve cached originals",
            handler=_handle_ts_command,
        )
