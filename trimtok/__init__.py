"""trimtok Hermes plugin adapter and Python package root. Glue over core/ccr/dedup."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .core import VERSION, compress_terminal
from .context_engine import ContextEngine, TrimtokContextEngine

__version__ = VERSION
_LOGGER = logging.getLogger("trimtok")
_REGISTERED_ENGINE: TrimtokContextEngine | None = None
_PRE_LLM_MAPPED_SESSIONS: OrderedDict[str, float] = OrderedDict()
_PRE_LLM_LOCK = threading.Lock()
_MAX_MAPPED_SESSIONS = 128

from .ccr import (
    default_dir,
    get as ccr_get,
    purge as ccr_purge,
    put as ccr_put,
    stats as ccr_stats,
    resolve_hash,
)
from .dedup import (
    clear_session,
    compress_tool_result,
    expire_old_turns,
    mask_old_turns,
    note_turn,
    record_hotspots,
    remember,
)
from .delta_engine import clear_session as delta_clear_session
from . import report
from .signals import (
    Deadline,
    VERBATIM_TOOLS,
    _READ_FILE_LIKE,
    estimate_tokens,
    is_important,
)
from .pruners import (
    group_search_hits,
    prune_traceback,
    prune_unified_diff,
    skeletonize_code,
)

_MAX_GREP_PATTERN_CHARS = 256
_MAX_GREP_INPUT_CHARS = 1_000_000
_MAX_GREP_MATCHES = 1000
_MAX_GREP_OUTPUT_CHARS = 100_000
_MAX_AUDIT_BYTES = 1_048_576
_MAX_USAGE_BYTES = 1_048_576
_AUDIT_LOCK = threading.Lock()

EXEC_SCHEMA = {
    "name": "trimtok_exec",
    "description": (
        "Run Python over a stored CCR '#ref' without loading it into context "
        "(read $TRIMTOK_PAYLOAD; only stdout returns). Use for counts, filters, "
        "or presence checks over large outputs. 10s default timeout, 4KB stdout cap."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hash": {"type": "string", "description": "CCR ref (e.g. '#c1') or 12-hex digest."},
            "code": {"type": "string", "description": "Python snippet reading os.environ['TRIMTOK_PAYLOAD']."},
            "timeout": {"type": "number", "description": "Timeout in seconds (default 10, max 30)."},
        },
        "required": ["hash", "code"],
        "additionalProperties": False,
    },
}


RETRIEVE_SCHEMA = {
    "name": "trimtok_retrieve",
    "description": (
        "Retrieve uncompressed original content by CCR '#ref' (or 12-hex digest), "
        "or pass 'query' for FTS5 BM25 search across stored payloads. Supports "
        "section slicing, grep, intent ranking, or offset/limit pagination."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hash": {"type": "string", "description": "CCR ref (e.g. '#c1') or 12-hex digest."},
            "query": {"type": "string", "description": "FTS5 BM25 keyword search across stored payloads."},
            "section": {
                "type": "string",
                "enum": ["errors", "diff", "summary", "tail"],
                "description": "Focused slice: errors, diff, summary, or tail.",
            },
            "context_lines": {"type": "integer", "description": "Lines around matches for errors/diff (0-20, default 3)."},
            "offset": {"type": "integer", "description": "1-indexed start line."},
            "limit": {"type": "integer", "description": "Max lines to return."},
            "grep": {"type": "string", "description": "Case-insensitive safe regex (no groups; max 256 chars)."},
            "intent": {"type": "string", "description": "Free-text question; returns top token-overlap lines."},
            "intent_budget": {"type": "integer", "description": "Max intent-ranked lines (default 400)."},
        },
        "anyOf": [{"required": ["hash"]}, {"required": ["query"]}],
    },
}


def _audit_path() -> Path:
    return default_dir() / "audit.jsonl"


def _usage_path() -> Path:
    return default_dir() / "usage.jsonl"


def _rotate_replace(path: Path, backup: Path) -> None:
    """Atomically rotate `path` to `backup` with bounded Windows file-watcher retry."""
    for attempt in range(3):
        try:
            os.replace(path, backup)
            return
        except PermissionError:
            if attempt == 2:
                path.write_text("", encoding="utf-8")
                return
            time.sleep(0.01 * (2 ** attempt))


def _append_jsonl(path: Path, entry: dict, max_bytes: int, oversize_event: str) -> None:
    """Append one JSONL row with size-bounded rotation under _AUDIT_LOCK."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    line_bytes = len(line.encode("utf-8"))
    if line_bytes > max_bytes:
        line = json.dumps({"event": oversize_event}) + "\n"
        line_bytes = len(line.encode("utf-8"))
    with _AUDIT_LOCK:
        backup = path.with_name(path.name + ".1")
        if backup.exists() and backup.stat().st_size > max_bytes:
            backup.unlink(missing_ok=True)
        if path.exists() and path.stat().st_size + line_bytes > max_bytes:
            _rotate_replace(path, backup)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _usage(entry: dict, ctx: Any = None) -> None:
    if ctx is not None and not _bool_cfg(ctx, "usage_enabled", True):
        return
    try:
        entry.setdefault("ts", int(time.time()))
        entry.setdefault("version", VERSION)
        if "trace_id" not in entry:
            tid = os.environ.get("TRINITY_TRACE_ID") or (_str_cfg(ctx, "trace_id", "") if ctx else "")
            if tid:
                entry["trace_id"] = str(tid)[:64]
        _append_jsonl(_usage_path(), entry, _MAX_USAGE_BYTES, "usage_entry_oversize")
    except Exception as exc:
        _LOGGER.warning("trimtok usage telemetry write failed: %s", exc)


def _after_chars(stats: dict, before: int) -> int:
    """Chars after compression, with passthrough gates counted as unchanged.

    Passthrough gates never set after_chars, so a row read after=0 and every
    aggregate of 1-after/before reported them as 100% saved (inflated the
    headline ~3.5x: real is ~9%, not ~44%).
    """
    after = int(stats.get("after_chars") or 0)
    return after if after else max(before - int(stats.get("saved_chars") or 0), 0)


def _reported_sizes(stats: dict, before: int) -> tuple[int, int]:
    if stats.get("gate") != "ok":
        return before, 0
    after = _after_chars(stats, before)
    return after, before - after


def _terminal_shape(stats: dict) -> str:
    kind = str(stats.get("kind") or stats.get("toon_kind") or "")
    if kind == "cmd_filter":
        return "cmd-filter"
    if kind in ("json", "json_toon", "json_toon_buckets", "json_toon_merged", "json_envelope"):
        return "json"
    if stats.get("partial") or stats.get("omitted_errors"):
        return "err-dense"
    if (stats.get("hidden_lines") or 0) > 0:
        return "repeat"
    if (stats.get("dropped_lines") or 0) > 0:
        return "unique"
    return "mixed"


def _tool_shape(stats: dict) -> str:
    kind = str(stats.get("kind") or stats.get("toon_kind") or "")
    if kind == "lockfile":
        return "lockfile"
    if kind in ("json_toon", "json_toon_buckets", "json_toon_merged", "json_envelope"):
        return "json"
    if kind == "file_envelope":
        return "json"
    if kind == "file_structure":
        return "doc-structure"
    if kind == "file_cache_hit":
        return "cache-hit"
    if kind == "file_delta":
        return "delta"
    if kind == "file_clean":
        return "minify"
    if stats.get("gate") == "passthrough_reference":
        # read_file verbatim band: its own shape, or it vanishes into "mixed"
        # and hides 15% of all tokens sitting at 0% saving.
        return "ref-verbatim"
    if (stats.get("fold_saved") or 0) > 0 or (stats.get("intra_saved") or 0) > 0:
        return "repeat"
    if (stats.get("headtail_saved") or 0) > 0:
        return "unique-large"
    if (stats.get("skeleton_saved") or 0) > 0:
        return "code-ast"
    if (stats.get("minify_saved") or 0) > 0 or (stats.get("toon_saved") or 0) > 0:
        return "json"
    if stats.get("gate") == "reverted_gate":
        return "unique"
    return "mixed"


def _usage_terminal(command: Any, output: Any, stats: dict, ctx: Any) -> None:
    try:
        out = output if isinstance(output, str) else ""
        after, saved = _reported_sizes(stats, len(out))
        _usage({
            "ts": int(time.time()),
            "hook": "terminal",
            "cmd": str(command or "")[:120],
            "kind": stats.get("kind"),
            "gate": stats.get("gate"),
            "before": len(out),
            "after": after,
            "saved": saved,
            "partial": bool(stats.get("partial", False)),
            "omitted": int(stats.get("omitted_errors") or 0),
            "ms": round(float(stats.get("ms", 0.0)), 2),
            "hash": str(stats.get("digest") or ""),
            "shape": _terminal_shape(stats),
            "reason": stats.get("reason"),
            "toon_saved": int(stats.get("toon_saved") or 0),
            "toon_kind": stats.get("toon_kind"),
            "toon_used": bool(stats.get("toon_used", False)),
        }, ctx=ctx)
    except Exception as exc:
        _LOGGER.warning("trimtok terminal telemetry entry failed: %s", exc)


def _usage_tool(tool_name: Any, result: Any, stats: dict, ctx: Any, args: Any = None) -> None:
    try:
        res = result if isinstance(result, str) else ""
        after, saved = _reported_sizes(stats, len(res))
        entry = {
            "ts": int(time.time()),
            "hook": "tool",
            "tool": str(tool_name or "tool"),
            "kind": stats.get("kind"),
            "gate": stats.get("gate"),
            "before": len(res),
            "after": after,
            "saved": saved,
            "ms": round(float(stats.get("ms", 0.0)), 2),
            "hash": str(stats.get("digest") or ""),
            "shape": _tool_shape(stats),
            "minify": int(stats.get("minify_saved") or 0),
            "fold": int(stats.get("fold_saved") or 0),
            "intra": int(stats.get("intra_saved") or 0),
            "intra_template_saved": int(stats.get("intra_template_saved") or 0),
            "skeleton": int(stats.get("skeleton_saved") or 0),
            "toon_saved": int(stats.get("toon_saved") or 0),
            "toon_kind": stats.get("toon_kind"),
            "toon_used": bool(stats.get("toon_used", False)),
            "reason": stats.get("reason"),
        }
        if str(tool_name or "") == "terminal" and isinstance(args, dict):
            cmd = args.get("command") or args.get("cmd")
            if cmd:
                entry["cmd"] = str(cmd)[:120]
        _usage(entry, ctx=ctx)
    except Exception as exc:
        _LOGGER.warning("trimtok tool telemetry entry failed: %s", exc)


def _usage_retrieve(args: dict, *, hit: bool, ctx: Any = None, **extra: Any) -> None:
    try:
        entry: dict = {
            "ts": int(time.time()),
            "hook": "retrieve",
            "hash": str((args or {}).get("hash") or "")[:16],
            "hit": bool(hit),
        }
        entry.update({k: v for k, v in extra.items() if v is not None})
        _usage(entry, ctx=ctx)
    except Exception as exc:
        _LOGGER.warning("trimtok retrieve telemetry entry failed: %s", exc)


def _audit(entry: dict, ctx: Any = None) -> None:
    if ctx is not None and not _bool_cfg(ctx, "audit_enabled", True):
        return
    try:
        entry.setdefault("ts", int(time.time()))
        entry.setdefault("version", VERSION)
        _append_jsonl(_audit_path(), entry, _MAX_AUDIT_BYTES, "audit_entry_oversize")
    except Exception as exc:
        _LOGGER.warning("trimtok audit telemetry write failed: %s", exc)


def _config_value(ctx: Any, key: str, default: Any) -> Any:
    if ctx is None:
        return default
    try:
        return ctx.get_config(key, default)
    except Exception:
        return default


def _int_cfg(ctx: Any, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(_config_value(ctx, key, default))
    except Exception:
        return default
    return max(low, min(high, value))


def _float_cfg(ctx: Any, key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(_config_value(ctx, key, default))
    except Exception:
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def _bool_cfg(ctx: Any, key: str, default: bool) -> bool:
    value = _config_value(ctx, key, default)
    return value if isinstance(value, bool) else default


def _str_cfg(ctx: Any, key: str, default: str) -> str:
    value = _config_value(ctx, key, default)
    return value if isinstance(value, str) else default


def _list_cfg(ctx: Any, key: str) -> list[str]:
    value = _config_value(ctx, key, [])
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


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


def _store_original(
    original: str, compressed: str, digest: str, ctx: Any
) -> tuple[str, str] | None:
    stored = ccr_put(
        original,
        max_entries=_int_cfg(ctx, "ccr_max_entries", 1000, 10, 100000),
        max_bytes=_int_cfg(ctx, "ccr_max_bytes", 50 * 1024 * 1024, 1024, 1_000_000_000),
        ttl_hours=_int_cfg(ctx, "ccr_ttl_hours", 24, 1, 24 * 30),
    )
    if not stored:
        return None
    if digest:
        old_ref = f"#{digest}"
        if stored != old_ref:
            compressed = compressed.replace(old_ref, stored)
    return compressed, stored


def _compile_grep(source: str) -> re.Pattern[str]:
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
            if char == "?" and last_was_repeat:
                last_was_repeat = False
                continue
            repeats += 1
            if repeats > 1:
                raise ValueError("only one repetition operator is supported in grep patterns")
            last_was_repeat = True
        else:
            last_was_repeat = False
    return re.compile(source, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Intent ranking (deterministic, zero-model): rank lines by token overlap with
# a free-text task description. This is the "smart selector" lane the E2E run
# showed buying ~14 more points of reduction without touching the compressor.
# ---------------------------------------------------------------------------
_INTENT_STOP = frozenset(
    "the a an and or of to in for on with what which when where who how "
    "does do did is are was were this that these those it they them their "
    "you your file line lines list show tell me give answer only value name "
    "any all each per new old first last most least from at by as if then out run".split()
)
_INTENT_TTERMS = re.compile(r"[a-z0-9_][a-z0-9_-]*", re.I)
# Definition lines carry the outline an agent needs even when the intent says
# "functions"/"signatures" and no code line contains that word.
_DEF_LINE_RE = re.compile(
    r"^\s*(?:\d+\|)?\s*(?:async\s+)?"
    r"(?:def|class|function|func|fn|interface|struct|impl|public|private|export|type)\b"
)
_DEF_INTENT = frozenset(
    "function functions def method methods class classes signature signatures "
    "define defines definition interface api outline symbol symbols".split()
)
# Headings answer "which versions/sections/releases" even when the heading text
# shares no vocabulary with the question.
_HEAD_LINE_RE = re.compile(r"^\s*(?:\d+\|)?\s*#{1,6}\s")
_HEAD_INTENT = frozenset(
    "version versions release releases changelog heading headings section sections "
    "title titles milestone milestones date dates".split()
)


def _stem(t: str) -> str:
    """Crude suffix strip so 'functions' matches 'function' and 'errors' 'error'."""
    for suf in ("ations", "ation", "ings", "ing", "ies", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def _intent_terms(intent: str) -> set[str]:
    terms: set[str] = set()
    for t in _INTENT_TTERMS.findall(intent):
        tl = t.lower()
        if len(tl) >= 3 and tl not in _INTENT_STOP:
            terms.add(_stem(tl))
    return terms


def _line_terms(line: str) -> set[str]:
    # For code lines prefer the raw token span; strip path noise but keep
    # identifiers and hex/digits.  This is a cheap bag-of-words overlap.
    return {_stem(t.lower()) for t in _INTENT_TTERMS.findall(line) if len(t) >= 2}


def _intent_rank(content: str, intent: str, budget: int, args: dict, ctx: Any) -> str:
    """Return the top-k lines most relevant to a free-text intent.

    Deterministic, O(n) bag-of-words overlap (exact token match only; no
    embedding model, no network). Keeps line-number prefixes and a compact
    header. Never raises -- falls back to head of the document.
    """
    terms = _intent_terms(intent)
    lines = content.splitlines()
    total = len(lines)
    wants_defs = bool(terms & _DEF_INTENT)
    wants_heads = bool(terms & _HEAD_INTENT)
    try:
        if not terms:
            raise ValueError("no usable terms")
        scored: list[tuple[int, int, str]] = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            lt = _line_terms(line)
            if not lt:
                continue
            overlap = len(lt & terms)
            # A definition line answers "what does this file define" even when
            # the intent word never appears in the source.
            if wants_defs and _DEF_LINE_RE.match(line):
                overlap += 2
            if wants_heads and _HEAD_LINE_RE.match(line):
                overlap += 2
            if overlap > 0:
                scored.append((overlap, i, line))
        scored.sort(key=lambda x: (-x[0], x[1]))
        chosen = scored[:max(1, min(budget, len(scored)))]
        if not chosen:
            chosen = [(0, i, l) for i, l in enumerate(lines[:min(budget, total)]) if l.strip()]
        # One line of context below each hit keeps a signature's body readable
        # without paying for the whole function.
        idx = {i for _, i, _ in chosen}
        for i in list(idx):
            if i + 1 < total and (i + 1) not in idx and len(idx) < budget:
                idx.add(i + 1)
        out_lines = [f"{i + 1}|{lines[i]}" for i in sorted(idx) if lines[i].strip()]
        body = "\n".join(out_lines)
        hit = len(out_lines)
        _usage_retrieve(args, hit=True, ctx=ctx, mode="intent", matches=hit, total=total)
        head = f"[tt:intent] {len(terms)} terms, {hit}/{total} lines by relevance (deterministic token overlap); full source in CCR #ref."
        return _ok({"intent": intent, "matched_lines": hit, "total_lines": total, "lines": head + "\n" + body})
    except Exception:
        # Fail-open: never error on intent; return the document head instead.
        head = min(budget, total)
        body = "\n".join(f"{i + 1}|{l}" for i, l in enumerate(lines[:head]) if l.strip())
        _usage_retrieve(args, hit=True, ctx=ctx, mode="intent_fallback", matches=head, total=total)
        return _ok({"intent": intent, "matched_lines": head, "total_lines": total, "lines": body})


def _finalize(
    raw: str,
    compressed: str,
    stats: dict,
    ctx: Any,
    audit_base: dict,
) -> str | None:
    if stats.get("gate") != "ok":
        return None
    if stats.get("lossless_tier0"):
        # v4.2 Tier 0: nothing was removed, so there is nothing to retrieve --
        # skip the SQLite CCR write entirely and ship the cleaned text.
        _audit({
            **audit_base,
            "before_chars": stats.get("before_chars"),
            "after_chars": stats.get("after_chars"),
            "saved_chars": stats.get("saved_chars"),
            "gate": "ok",
            "hash": "tier0_lossless",
            "ms": round(float(stats.get("ms", 0.0)), 2),
        }, ctx=ctx)
        return compressed
    stored = _store_original(raw, compressed, str(stats.get("digest", "")), ctx)
    if stored is None:
        return None
    new_text, digest = stored
    if _REGISTERED_ENGINE is not None and digest:
        try:
            _REGISTERED_ENGINE._record_session_ref(digest)
        except Exception:
            pass
    _audit({
        **audit_base,
        "before_chars": stats.get("before_chars"),
        "after_chars": stats.get("after_chars"),
        "saved_chars": stats.get("saved_chars"),
        "gate": "ok",
        "hash": digest,
        "ms": round(float(stats.get("ms", 0.0)), 2),
    }, ctx=ctx)
    return new_text


def _storage_failure_stats(stats: dict, original: str) -> dict:
    if stats.get("gate") != "ok":
        return stats
    failed = dict(stats)
    failed.update(
        gate="passthrough_storage_failure",
        before_chars=len(original),
        after_chars=len(original),
        saved_chars=0,
        reason="ccr_store_failed",
    )
    return failed


_HANDLED_TERMINAL_CALLS: OrderedDict[str, float] = OrderedDict()
_TERMINAL_CALL_LOCK = threading.Lock()
_MAX_HANDLED_TERMINAL_CALLS = 128


def _mark_terminal_call(call_id: Any) -> None:
    cid = str(call_id or "").strip()
    if not cid:
        return
    with _TERMINAL_CALL_LOCK:
        _HANDLED_TERMINAL_CALLS.pop(cid, None)
        _HANDLED_TERMINAL_CALLS[cid] = time.monotonic()
        while len(_HANDLED_TERMINAL_CALLS) > _MAX_HANDLED_TERMINAL_CALLS:
            _HANDLED_TERMINAL_CALLS.popitem(last=False)


def _consume_terminal_call(call_id: Any) -> bool:
    cid = str(call_id or "").strip()
    if not cid:
        return False
    with _TERMINAL_CALL_LOCK:
        ts = _HANDLED_TERMINAL_CALLS.pop(cid, None)
        if ts is None:
            return False
        return (time.monotonic() - ts) < 60.0


def _on_pre_tool_call(
    tool_name: Any = None,
    args: Any = None,
    **kw: Any,
) -> dict[str, str] | None:
    """Block mutating tool calls that attempt to persist compressed Trimtok stubs."""
    ctx = getattr(_on_pre_tool_call, "_ctx", None)
    if ctx is not None and (not _bool_cfg(ctx, "enabled", True) or _bool_cfg(ctx, "allow_stub_write", False)):
        return None
    effective_args = args if args is not None else (
        kw.get("tool_args") or kw.get("arguments") or kw.get("input") or kw
    )
    try:
        from .guard import check_write_args
    except ImportError:
        from guard import check_write_args  # type: ignore
    try:
        hit = check_write_args(str(tool_name or ""), effective_args)
        if hit:
            return {"action": "block", "message": hit["message"]}
    except Exception:
        pass
    return None


def _on_transform(
    command: Any = None,
    output: Any = None,
    returncode: Any = 0,
    **kw: Any,
) -> str | None:
    ctx = getattr(_on_transform, "_ctx", None)
    if ctx is not None and not _bool_cfg(ctx, "enabled", True):
        return None
    if not isinstance(output, str):
        return None
    if _excluded_command(command, ctx):
        _mark_terminal_call(kw.get("tool_call_id"))
        return None
    sid = str(kw.get("session_id") or kw.get("task_id") or "default")
    exit_code = _safe_int(returncode, 0)
    record_hotspots(sid, output, exit_code)
    _mark_terminal_call(kw.get("tool_call_id"))
    min_chars = _int_cfg(ctx, "min_chars", 2048, 0, 10_000_000)
    new_output, stats = compress_terminal(
        str(command or ""),
        output,
        min_chars=min_chars,
        max_lines=_int_cfg(ctx, "max_lines", 120, 10, 10000),
        session_id=sid,
        delta_enabled=_bool_cfg(ctx, "delta_enabled", True),
        delta_threshold=_float_cfg(ctx, "delta_threshold", 0.65, 0.5, 1.0),
        deadline=Deadline(_float_cfg(ctx, "guard_ms", 200.0, 1.0, 1000.0)),
        pressure=_context_pressure(ctx, kw),
        exit_code=exit_code,
        policy_enabled=_bool_cfg(ctx, "policy_enabled", True),
    )
    out = _finalize(output, new_output, stats, ctx, {
        "tool": "terminal",
        "command": str(command or "")[:120],
        "kind": stats.get("kind"),
    })
    if stats.get("gate") == "ok" and out is None:
        stats = _storage_failure_stats(stats, output)
    _usage_terminal(command, output, stats, ctx)
    return out


def _toon_allowed(ctx: Any) -> bool:
    """Model-aware TOON gate.

    TOON is plain text, so every model *can* read it; what differs is how well a
    model holds a columnar table together. `toon_mode` lets the caller decide:

      always  -- emit TOON whenever it wins on tokens (opt-in for known-good)
      never   -- always fall back to minified JSON
      auto    -- TOON for model families with a track record on columnar text,
                 minified JSON for everything else (default when `model_family`
                 is set; TOON when it is not, so nothing regresses silently)
    """
    if not _bool_cfg(ctx, "toon_enabled", True):
        return False
    mode = str(_str_cfg(ctx, "toon_mode", "auto") or "auto").strip().lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    family = str(_str_cfg(ctx, "model_family", "") or "").strip().lower()
    if not family and _REGISTERED_ENGINE is not None:
        family = str(getattr(_REGISTERED_ENGINE, "active_model_family", "") or "").strip().lower()
    if not family:
        return True  # unknown caller: keep the existing behaviour
    try:
        from .policy import toon_allowed_for as _ok
        return _ok(family, mode)
    except Exception:
        return True


def _context_pressure(ctx: Any, kw: dict) -> float:
    """Fraction of the context window already used, from hook kwargs, static config, or live engine."""
    if not _bool_cfg(ctx, "adaptive_enabled", True):
        return 0.0
    for key in ("context_pressure", "pressure", "context_used_pct", "context_usage"):
        v = kw.get(key)
        if v is None:
            continue
        try:
            f = float(v)
        except Exception:
            continue
        if not math.isfinite(f):
            continue
        if f > 1.0:
            f = f / 100.0
        return max(0.0, min(1.0, f))
    cfg_p = _float_cfg(ctx, "context_pressure", 0.0, 0.0, 1.0)
    if cfg_p > 0.0:
        return cfg_p
    if _REGISTERED_ENGINE is not None:
        try:
            live = float(_REGISTERED_ENGINE.live_pressure)
            if math.isfinite(live) and live > 0.0:
                return max(0.0, min(1.0, live))
        except Exception:
            pass
    return 0.0


def _on_tool_result(
    tool_name: Any = None,
    args: Any = None,
    result: Any = None,
    session_id: Any = None,
    task_id: Any = None,
    turn_id: Any = None,
    **kw: Any,
) -> str | None:
    ctx = getattr(_on_tool_result, "_ctx", None)
    if ctx is not None and not _bool_cfg(ctx, "enabled", True):
        return None
    if ctx is not None and not _bool_cfg(ctx, "phase2_enabled", True):
        return None
    if not isinstance(result, str):
        return None
    if _excluded_tool(tool_name, ctx):
        return None
    verbatim_name = str(tool_name or "").strip()
    if verbatim_name == "terminal" and _consume_terminal_call(kw.get("tool_call_id")):
        return None
    if verbatim_name in VERBATIM_TOOLS and verbatim_name not in _READ_FILE_LIKE:
        return None
    sid = str(session_id or task_id or "default")
    turn = str(turn_id or "")
    effective_args = args if args is not None else (
        kw.get("tool_args") or kw.get("arguments") or kw.get("input"))
    if verbatim_name == "terminal" and isinstance(effective_args, dict):
        if _excluded_command(effective_args.get("command") or effective_args.get("cmd"), ctx):
            return None
    new_result, stats = compress_tool_result(
        str(tool_name or ""),
        result,
        args=effective_args,
        session_id=sid,
        min_chars=_int_cfg(ctx, "min_chars", 2048, 0, 10_000_000),
        toon_enabled=_toon_allowed(ctx),
        policy_enabled=_bool_cfg(ctx, "policy_enabled", True),
        deadline=Deadline(_float_cfg(ctx, "guard_ms", 200.0, 1.0, 1000.0)),
        pressure=_context_pressure(ctx, kw),
        delta_enabled=_bool_cfg(ctx, "delta_enabled", True),
        delta_threshold=_float_cfg(ctx, "delta_threshold", 0.65, 0.5, 1.0),
        max_lines=_int_cfg(ctx, "max_lines", 120, 10, 10000),
    )
    out = _finalize(result, new_result, stats, ctx, {
        "tool": str(tool_name or "tool"),
        "kind": "tool_result",
    })
    if stats.get("gate") == "ok" and out is None:
        stats = _storage_failure_stats(stats, result)
    _usage_tool(tool_name, result, stats, ctx, args=effective_args)
    if out is not None or stats.get("gate") == "passthrough_reference":
        # note_turn even for a verbatim read: expire_old_turns ages the file
        # cache by turn, and a read that is never noted never expires.
        note_turn(result, str(stats.get("digest", "")), session_id=sid, turn_id=turn)
    return out


def _on_post_llm(
    session_id: Any = None,
    task_id: Any = None,
    turn_id: Any = None,
    **kw: Any,
) -> None:
    ctx = getattr(_on_post_llm, "_ctx", None)
    if ctx is not None and (not _bool_cfg(ctx, "enabled", True) or not _bool_cfg(ctx, "mask_enabled", True)):
        return None
    sid = str(session_id or task_id or "default")
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


def _parse_ref(raw: Any) -> str:
    ref = str(raw or "").strip().strip("<>").strip()
    ref = ref.removeprefix("tt:").split(",")[0].strip()
    ref = ref.removeprefix("retrieve:").strip()
    if ref.startswith("#"):
        return "#" + re.sub(r"[^0-9A-Za-z]", "", ref[1:])[:16]
    if re.match(r"^c[0-9A-Za-z]+$", ref) and len(ref) <= 8:
        return "#" + ref
    if re.fullmatch(r"[0-9a-fA-F]{6,64}", ref):
        return ref.lower()[:16]
    hex_ref = "".join(c for c in ref.lower() if c in "0123456789abcdef")[:16]
    if hex_ref:
        return hex_ref
    return re.sub(r"[^0-9A-Za-z]", "", ref)[:16]


def _safe_int(val: Any, default: int) -> int:
    try:
        if val is None or isinstance(val, bool):
            return default
        return int(val)
    except Exception:
        return default


def _safe_float(val: Any, default: float) -> float:
    try:
        if val is None or isinstance(val, bool):
            return default
        f = float(val)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _render_context_intervals(
    lines: list[str], match_indices: list[int], ctx_lines: int
) -> tuple[str, int]:
    total_lines = len(lines)
    intervals: list[tuple[int, int]] = []
    for idx in match_indices:
        s = max(0, idx - ctx_lines)
        e = min(total_lines, idx + ctx_lines + 1)
        if not intervals:
            intervals.append((s, e))
        else:
            last_s, last_e = intervals[-1]
            if s <= last_e:
                intervals[-1] = (last_s, max(last_e, e))
            else:
                intervals.append((s, e))
    chunks: list[str] = []
    returned_lines = 0
    for s, e in intervals:
        chunk_lines = [f"{i + 1}|{lines[i]}" for i in range(s, e)]
        returned_lines += len(chunk_lines)
        chunks.append("\n".join(chunk_lines))
    return "\n[...]\n".join(chunks), returned_lines


def _usage_exec(args: dict, res: dict, ctx: Any = None) -> None:
    try:
        out_str = str(res.get("output") or "")
        _usage({
            "ts": int(time.time()),
            "hook": "exec",
            "hash": str((args or {}).get("hash") or "")[:16],
            "ok": bool(res.get("ok")),
            "exit_code": int(res.get("exit_code") or 0),
            "payload_bytes": int(res.get("payload_bytes") or 0),
            "out_chars": len(out_str),
            "ms": round(float(res.get("ms") or 0.0), 2),
        }, ctx=ctx)
    except Exception as exc:
        _LOGGER.warning("trimtok exec telemetry entry failed: %s", exc)


def _handle_exec(args: dict, **kw: Any) -> str:
    from .exec_lane import run as _exec_run
    ctx = getattr(_handle_exec, "_ctx", None)
    a = args or {}
    ref = _parse_ref(a.get("hash"))
    if not ref:
        return _err("hash is required (e.g. '#c1')")
    code = str(a.get("code") or "")
    if not code.strip():
        return _err("code is required")
    raw_to = _safe_float(a.get("timeout"), 10.0)
    timeout = max(0.1, min(30.0, raw_to if raw_to > 0.0 else 10.0))
    ttl_hours = _int_cfg(ctx, "ccr_ttl_hours", 24, 1, 720)
    res = _exec_run(ref, code, timeout=timeout, ttl_hours=ttl_hours)
    _usage_exec(a, res, ctx=ctx)
    if res.get("ok"):
        return _ok(res)
    return _err(f"exec failed (exit {res.get('exit_code')}): {str(res.get('output'))[:600]}")


def _retrieve_by_query(args: dict, query: str, ttl_hours: int) -> str:
    """FTS5 lookup when the caller passes a query instead of a ref."""
    if not ccr.fts_ready():
        return _err("FTS5 search is unavailable in this SQLite build; use hash retrieval instead.")
    limit = max(1, min(50, _safe_int((args or {}).get("limit"), 5)))
    hits = ccr.search(query, limit=limit, ttl_hours=ttl_hours)
    ctx = getattr(_handle_retrieve, "_ctx", None)
    _usage_retrieve(args, hit=bool(hits), ctx=ctx, mode="query", matches=len(hits))
    return _ok({"hits": hits, "count": len(hits), "query": query})


def _retrieve_by_grep(content: str, grep: str, args: dict, ctx: Any) -> str:
    """Line grep over one stored payload, bounded by the _MAX_GREP_* caps."""
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
        _usage_retrieve(args, hit=True, ctx=ctx, mode="grep",
                        matches=matches_count, truncated=truncated)
        return _ok({
            "matches": "\n".join(matched) if matched else "(no matches found)",
            "matches_count": matches_count,
            "total_lines": total_lines,
            "truncated": truncated,
        })
    except Exception as e:
        return _err(f"Invalid grep pattern: {e}")


def _retrieve_by_section(
    content: str, lines: list[str], total_lines: int, args: dict, ctx: Any
) -> str | None:
    """section=errors|diff|summary|tail view. None when no section was asked for."""
    section = str((args or {}).get("section") or "").strip().lower()
    if not section:
        return None
    ctx_lines = max(0, min(20, _safe_int((args or {}).get("context_lines"), 3)))
    if section == "errors":
        match_indices = [i for i, line in enumerate(lines) if is_important(line)]
        if not match_indices:
            _usage_retrieve(args, hit=True, ctx=ctx, mode="section_errors", matches=0)
            return _ok({
                "section": "errors",
                "error_matches": 0,
                "total_lines": total_lines,
                "lines": "(no error lines detected)",
            })
        formatted, returned_lines = _render_context_intervals(lines, match_indices, ctx_lines)
        _usage_retrieve(args, hit=True, ctx=ctx, mode="section_errors",
                        matches=len(match_indices), returned=returned_lines, total=total_lines)
        return _ok({
            "section": "errors",
            "error_matches": len(match_indices),
            "returned_lines": returned_lines,
            "total_lines": total_lines,
            "lines": formatted,
        })
    if section == "diff":
        diff_indices = []
        diff_prefixes = ("diff --git ", "--- ", "+++ ", "index ", "similarity index ", "new file mode ", "deleted file mode ")
        for i, line in enumerate(lines):
            if line.startswith(diff_prefixes) or line.startswith("@@ "):
                diff_indices.append(i)
            elif (line.startswith("+") and not line.startswith("+++")) or (line.startswith("-") and not line.startswith("---")):
                diff_indices.append(i)
        if not diff_indices:
            _usage_retrieve(args, hit=True, ctx=ctx, mode="section_diff", matches=0)
            return _ok({
                "section": "diff",
                "diff_matches": 0,
                "total_lines": total_lines,
                "lines": "(no diff hunks or changes detected)",
            })
        formatted, returned_lines = _render_context_intervals(lines, diff_indices, ctx_lines)
        _usage_retrieve(args, hit=True, ctx=ctx, mode="section_diff",
                        matches=len(diff_indices), returned=returned_lines, total=total_lines)
        return _ok({
            "section": "diff",
            "diff_matches": len(diff_indices),
            "returned_lines": returned_lines,
            "total_lines": total_lines,
            "lines": formatted,
        })
    if section == "summary":
        error_indices = [i for i, line in enumerate(lines) if is_important(line)]
        head_count = min(15, total_lines)
        head_lines = [f"{i + 1}|{lines[i]}" for i in range(head_count)]
        tail_start = max(head_count, total_lines - 25)
        tail_lines = [f"{i + 1}|{lines[i]}" for i in range(tail_start, total_lines)]
        top_errors = [f"{i + 1}|{lines[i]}" for i in error_indices[:20]]
        _usage_retrieve(args, hit=True, ctx=ctx, mode="section_summary",
                        errors=len(error_indices), total=total_lines)
        return _ok({
            "section": "summary",
            "total_lines": total_lines,
            "total_chars": len(content),
            "error_lines_count": len(error_indices),
            "head_lines": "\n".join(head_lines),
            "tail_lines": "\n".join(tail_lines),
            "top_errors": "\n".join(top_errors) if top_errors else "(none)",
        })
    if section == "tail":
        limit = _safe_int((args or {}).get("limit"), 50)
        cnt = max(1, min(1000, limit))
        tail_start = max(0, total_lines - cnt)
        tail_slice = [f"{tail_start + i + 1}|{line}" for i, line in enumerate(lines[tail_start:])]
        _usage_retrieve(args, hit=True, ctx=ctx, mode="section_tail",
                        returned=len(tail_slice), total=total_lines)
        return _ok({
            "section": "tail",
            "returned_lines": len(tail_slice),
            "total_lines": total_lines,
            "lines": "\n".join(tail_slice),
        })
    return _err(f"Invalid section '{section}'. Allowed: 'errors', 'diff', 'summary', 'tail'")


def _retrieve_by_slice(lines: list[str], total_lines: int, args: dict, ctx: Any) -> str | None:
    """offset/limit view. None when neither was passed."""
    offset = (args or {}).get("offset")
    limit = (args or {}).get("limit")
    if offset is None and limit is None:
        return None
    start = max(0, _safe_int(offset, 1) - 1)
    cnt = _safe_int(limit, 1000)
    end = min(total_lines, start + max(1, cnt))
    sliced = [f"{i + 1}|{lines[i]}" for i in range(start, end)]
    _usage_retrieve(args, hit=True, ctx=ctx, mode="slice",
                    offset=start + 1, returned=len(sliced), total=total_lines)
    return _ok({
        "lines": "\n".join(sliced),
        "offset": start + 1,
        "total_lines": total_lines,
        "returned_lines": len(sliced),
    })


def _handle_retrieve(args: dict, **kw: Any) -> str:
    ctx = getattr(_handle_retrieve, "_ctx", None)
    ttl_hours = _int_cfg(ctx, "ccr_ttl_hours", 24, 1, 24 * 30) if ctx is not None else 24
    ref = _parse_ref((args or {}).get("hash"))
    query = str((args or {}).get("query") or "").strip()
    if query and not ref:
        return _retrieve_by_query(args, query, ttl_hours)
    if not ref:
        return _err("hash or query is required (e.g. '#c1' or search query)")
    content = ccr_get(ref, ttl_hours=ttl_hours)
    if content is None:
        _usage_retrieve(args, hit=False, ctx=ctx)
        return _err("Not found or expired (TTL passed). Re-run the original command instead.")

    intent = str((args or {}).get("intent") or "").strip()
    intent_budget = _safe_int((args or {}).get("intent_budget"), 400)
    if intent and intent_budget > 0:
        return _intent_rank(content, intent, intent_budget, args, ctx)

    grep = str((args or {}).get("grep") or "").strip()
    if grep:
        return _retrieve_by_grep(content, grep, args, ctx)

    lines = content.splitlines()
    total_lines = len(lines)
    view = _retrieve_by_section(content, lines, total_lines, args, ctx)
    if view is not None:
        return view
    view = _retrieve_by_slice(lines, total_lines, args, ctx)
    if view is not None:
        return view
    _usage_retrieve(args, hit=True, ctx=ctx, mode="full", chars=len(content))
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


def _iter_usage_rows():
    """Usage rows for this install; the path comes from `_usage_path` so tests can patch it."""
    return report.iter_usage_rows(_usage_path(), _AUDIT_LOCK)


def _discover_summary() -> str:
    return report.discover_summary(_usage_path(), _AUDIT_LOCK)


def _gain_summary(days: int = 0) -> str:
    return report.gain_summary(_usage_path(), days, _AUDIT_LOCK)


def _usage_summary() -> str:
    return report.usage_summary(_usage_path(), _AUDIT_LOCK)




def _handle_ts_command(raw_args: str) -> str:
    arg_raw = (raw_args or "").strip()
    head, _, rest = arg_raw.partition(" ")
    verb = head.lower()
    arg_rest = rest.strip()
    arg = f"{verb} {arg_rest}".strip() if verb in ("gain", "recall", "retrieve") else arg_raw.lower()
    if arg in ("", "status"):
        try:
            path = _audit_path()
            backup = path.with_name(path.name + ".1")
            lines = []
            with _AUDIT_LOCK:
                for audit_file in (backup, path):
                    if audit_file.exists():
                        lines.extend(audit_file.read_text(encoding="utf-8").splitlines())
            saved = 0
            transforms = 0
            for ln in lines:
                s = ln.strip()
                if not s.startswith("{"):
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue
                if not isinstance(obj, dict) or "event" in obj or obj.get("tool") == "mask":
                    continue
                transforms += 1
                saved += int(obj.get("saved_chars") or 0)
            c_stat = ccr_stats()
            return (
                f"trimtok: {transforms} transforms logged (recent window), ~{saved:,} chars saved.\n"
                f"CCR Cache: {c_stat.get('entries', 0)} entries (~{c_stat.get('bytes', 0) // 1024} KB)."
            )
        except Exception:
            return "trimtok: status unavailable."
    if arg == "stats":
        return _handle_ts_command("status")
    if arg == "doctor":
        return json.dumps(report.run_doctor(), indent=2, ensure_ascii=False)
    if arg == "usage":
        return _usage_summary()
    parts = arg.split()
    verb = parts[0].lower() if parts else ""
    if verb == "gain":
        days = 0
        if len(parts) >= 2:
            try:
                days = max(0, int(parts[1]))
            except ValueError:
                pass
        return _gain_summary(days=days)
    if verb == "discover":
        return _discover_summary()
    if verb in ("recall", "retrieve"):
        rest = parts[1:]
        if len(rest) >= 2 and rest[1].lower() in ("errors", "diff", "summary", "tail"):
            return _handle_retrieve({"hash": rest[0], "section": rest[1].lower()})
        return _handle_retrieve({"hash": rest[0] if rest else ""})
    if verb == "purge":
        purged = ccr_purge()
        return f"trimtok: purged {purged} cached original files."
    return "Usage: /trimtok [status|stats|doctor|usage|gain|discover|recall|purge|retrieve <hash> [section]]"


def _on_session_reset(session_id: Any = None, **kw: Any) -> None:
    sid = str(session_id or kw.get("task_id") or "").strip()
    if not sid:
        return
    clear_session(sid)
    delta_clear_session(sid)
    with _PRE_LLM_LOCK:
        _PRE_LLM_MAPPED_SESSIONS.pop(sid, None)


def _persist_agent_payload(raw: str, new_text: str, stats: dict) -> tuple[str, dict]:
    if stats.get("gate") != "ok" or stats.get("lossless_tier0"):
        return new_text, stats
    digest = str(stats.get("digest") or "")
    ref = ccr_put(raw)
    if not ref:
        return raw, _storage_failure_stats(stats, raw)
    if digest and f"#{digest}" in new_text:
        new_text = new_text.replace(f"#{digest}", ref)
    return new_text, stats


def compress_agent_payload(
    payload: dict | str, mode: str = "tool", **kw: Any
) -> tuple[dict | str, dict]:
    try:
        if isinstance(payload, str):
            if mode == "terminal":
                new_text, stats = compress_terminal(kw.get("command", ""), payload)
            else:
                new_text, stats = compress_tool_result(
                    kw.get("tool_name", "agent"), payload,
                    session_id=str(kw.get("session_id", "")),
                )
            return _persist_agent_payload(payload, new_text, stats)
        if isinstance(payload, dict):
            for key in ("content", "output", "stdout", "result"):
                if isinstance(payload.get(key), str):
                    raw = payload[key]
                    if mode == "terminal":
                        new_text, stats = compress_terminal(kw.get("command", ""), raw)
                    else:
                        new_text, stats = compress_tool_result(
                            str(payload.get("tool_name") or kw.get("tool_name", "agent")),
                            raw,
                            session_id=str(payload.get("session_id") or kw.get("session_id", "")),
                        )
                    new_text, stats = _persist_agent_payload(raw, new_text, stats)
                    return {**payload, key: new_text}, stats
        return payload, {"gate": "passthrough_non_str"}
    except Exception:
        return payload, {"gate": "passthrough_exception"}


_PROJECT_MARKERS = (
    ".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
    "setup.py", "setup.cfg", "pom.xml", "build.gradle", "Gemfile", "composer.json",
)


def _project_root(start: Path | None = None) -> Path | None:
    """Nearest directory that looks like a project, or None.

    The hook used to map whatever `Path.cwd()` happened to be. Inside a host
    app that is the app's own home (e.g. ~/AppData/Local/hermes), so every
    session was handed a 1.5 KB map of the uv cache instead of the repo.
    """
    root = (start or Path.cwd()).resolve()
    for candidate in (root, *root.parents):
        if any((candidate / marker).exists() for marker in _PROJECT_MARKERS):
            return candidate
        if candidate == candidate.parent:
            break
    return None


def _contains_codemap(val: Any) -> bool:
    if isinstance(val, str):
        return "Codebase map (" in val
    if isinstance(val, list):
        for part in val:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and "Codebase map (" in part["text"]:
                return True
            if isinstance(part, str) and "Codebase map (" in part:
                return True
    return False


def _pre_llm_call(session_id: str = "", conversation_history: Any = None, **_: Any) -> dict[str, str] | None:
    ctx = getattr(_pre_llm_call, "_ctx", None)
    if ctx is not None and not _bool_cfg(ctx, "enabled", True):
        return None
    if os.environ.get("TRIMTOK_MAP", "1") == "0":
        return None
    sid = str(session_id or "").strip()
    if sid:
        with _PRE_LLM_LOCK:
            if sid in _PRE_LLM_MAPPED_SESSIONS:
                return None
    for message in reversed(conversation_history or []):
        if not isinstance(message, dict):
            continue
        for _fld in ("api_content", "content"):
            if _contains_codemap(message.get(_fld)):
                return None
    try:
        from .codemap import build_codebase_map
    except ImportError:
        from codemap import build_codebase_map  # type: ignore
    try:
        root = _project_root()
        if root is None:
            return None
        cmap = build_codebase_map(root=root, budget_chars=1500)
        if cmap:
            if sid:
                with _PRE_LLM_LOCK:
                    _PRE_LLM_MAPPED_SESSIONS[sid] = time.monotonic()
                    while len(_PRE_LLM_MAPPED_SESSIONS) > _MAX_MAPPED_SESSIONS:
                        _PRE_LLM_MAPPED_SESSIONS.popitem(last=False)
            return {"context": cmap}
    except Exception:
        pass
    return None


def register(ctx: Any) -> None:
    global _REGISTERED_ENGINE
    _on_pre_tool_call._ctx = ctx  # type: ignore[attr-defined]
    _pre_llm_call._ctx = ctx  # type: ignore[attr-defined]
    _on_transform._ctx = ctx  # type: ignore[attr-defined]
    _on_tool_result._ctx = ctx  # type: ignore[attr-defined]
    _on_post_llm._ctx = ctx  # type: ignore[attr-defined]
    _handle_retrieve._ctx = ctx  # type: ignore[attr-defined]
    _handle_exec._ctx = ctx  # type: ignore[attr-defined]
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_terminal_output", _on_transform)
    ctx.register_hook("transform_tool_result", _on_tool_result)
    ctx.register_hook("post_llm_call", _on_post_llm)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("on_session_start", _on_session_reset)
    ctx.register_hook("on_session_end", _on_session_reset)
    ctx.register_tool(
        name="trimtok_retrieve",
        toolset="trimtok",
        schema=RETRIEVE_SCHEMA,
        handler=_handle_retrieve,
        emoji="\U0001f9f9",
    )
    ctx.register_tool(
        name="trimtok_exec",
        toolset="trimtok",
        schema=EXEC_SCHEMA,
        handler=_handle_exec,
        emoji="🧮",
    )
    if hasattr(ctx, "register_command"):
        ctx.register_command(
            "trimtok",
            _handle_ts_command,
            "View trimtok token savings, status, or retrieve cached originals",
        )
    elif hasattr(ctx, "register_slash_command"):
        ctx.register_slash_command(
            name="trimtok",
            description="View trimtok token savings, status, or retrieve cached originals",
            handler=_handle_ts_command,
        )
    if hasattr(ctx, "register_context_engine") and _bool_cfg(ctx, "enabled", True):
        engine = TrimtokContextEngine(
            min_stash_chars=_int_cfg(ctx, "min_chars", 2048, 256, 10_000_000),
            policy_enabled=_bool_cfg(ctx, "policy_enabled", True),
            ccr_max_entries=_int_cfg(ctx, "ccr_max_entries", 1000, 10, 100_000),
            ccr_max_bytes=_int_cfg(ctx, "ccr_max_bytes", 52_428_800, 1024 * 1024, 2_000_000_000),
            ccr_ttl_hours=_int_cfg(ctx, "ccr_ttl_hours", 24, 1, 720),
        )
        _REGISTERED_ENGINE = engine
        ctx.register_context_engine(engine)


