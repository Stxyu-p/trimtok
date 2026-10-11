"""trimtok Hermes plugin adapter. Glue over core.py/ccr.py/dedup.py."""

from __future__ import annotations

import json
import logging
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

RETRIEVE_SCHEMA = {
    "name": "trimtok_retrieve",
    "description": (
        "Retrieve the full uncompressed original content of a previously folded or windowed "
        "tool result by its content hash. Use this when you need specific details from "
        "a result marked with '[tt:terminal ... retrieve:<hash>]' or '[tt:tool ... retrieve:<hash>]'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hash": {
                "type": "string",
                "description": "The 12-character content hash shown in the [tt:... retrieve:<hash>] marker.",
            },
        },
        "required": ["hash"],
    },
}


def _audit_path() -> Path:
    return default_dir() / "audit.jsonl"


def _audit(entry: dict) -> None:
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
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
    })
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
    })
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
            _audit({"tool": "mask", "expired_turns": expired, "gate": "ok", "ms": 0.0})
    except Exception:
        pass
    return None


def _handle_retrieve(args: dict, **kw: Any) -> str:
    digest = str((args or {}).get("hash") or "").strip().strip("<>").removeprefix("tt:").split(",")[0].strip()
    digest = "".join(c for c in digest if c in "0123456789abcdef")[:16]
    if not digest:
        return _err("hash is required (from a '[tt:... retrieve:<hash>]' marker)")
    content = ccr_get(digest)
    if content is None:
        return _err("Not found or expired (TTL passed). Re-run the original command instead.")
    return _ok({"original_content": content, "original_chars": len(content)})


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
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            saved = sum((json.loads(ln).get("saved_chars") or 0) for ln in lines if ln.strip().startswith("{"))
            c_stat = ccr_stats()
            return (
                f"trimtok v{VERSION}: {len(lines)} transforms logged, ~{saved:,} chars saved.\n"
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


def register(ctx: Any) -> None:
    _on_transform._ctx = ctx  # type: ignore[attr-defined]
    _on_tool_result._ctx = ctx  # type: ignore[attr-defined]
    _on_post_llm._ctx = ctx  # type: ignore[attr-defined]
    ctx.register_hook("transform_terminal_output", _on_transform)
    ctx.register_hook("transform_tool_result", _on_tool_result)
    ctx.register_hook("post_llm_call", _on_post_llm)
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
