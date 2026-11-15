"""JSON-shaped tool lanes: web intake, session caches, prefix folding, session fold.

Each lane is one step of `dedup.compress_tool_result`: it takes the tool name and
the raw payload, returns `(text, stats)` when it compressed something, and
returns `None` when it has nothing to say. None of them raises -- a lane that
cannot parse its payload must leave the pipeline exactly as it found it.

They live here, not in `dedup.py`, so the orchestrator reads as an ordered list
of decisions instead of a wall of nested branches.
"""

from __future__ import annotations

import hashlib
import json as _json
import os
import time
from typing import Any

from .signals import adaptive_tool_head, has_marker_head, tok_pair as _tok_pair

WEB_TOOLS = frozenset({"web_extract", "web_search", "browser_exec"})


def digest(text: str) -> str:
    """CCR digest for one payload (12 hex chars, matching the retrieve refs)."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _stats(result: str, final: str, tb: int, ta: int, kind: str, rh: str, t0: float) -> dict:
    return {
        "gate": "ok",
        "kind": kind,
        "digest": rh,
        "before_chars": len(result),
        "after_chars": len(final),
        "saved_chars": len(result) - len(final),
        "est_tokens_before": tb,
        "est_tokens_after": ta,
        "ms": (time.perf_counter() - t0) * 1000,
    }


def _head_and_check(result: str, cand: str, ref: str) -> tuple[str, int, int] | None:
    """Prefix the CCR header and re-check the token gate on the final text."""
    tb, ta = _tok_pair(result, cand)
    head = adaptive_tool_head(len(result) - len(cand), ref.lstrip("#"), len(result), is_lossless_only=False)
    final = f"{head}\n{cand}" if head else cand
    _, final_ta = _tok_pair(result, final)
    if len(final) < len(result) and final_ta < tb:
        return final, tb, final_ta
    return None


def web_intake(tool_name: str, result: str, t0: float) -> tuple[str, dict] | None:
    """Shave web payloads with the web/doc lane instead of the terminal window.

    `web_extract`/`browser_exec` ship JSON whose `results[].content` (or a plain
    text body) is markdown. Sending that whole envelope to
    `fold_envelope_text` -> `compress_terminal(max_lines=60)` cut an article in
    half and stamped a terminal header on it.
    """
    from .ccr import put as _ccr_put
    from .strategies.tiered_shave import shave_tiered

    if tool_name not in WEB_TOOLS or has_marker_head(result):
        return None
    rh = digest(result)
    stripped = result.lstrip()
    if stripped.startswith("{") and tool_name in ("web_extract", "browser_exec"):
        try:
            data = _json.loads(result)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return None
        ref = _ccr_put(result)
        ref_tag = f"#{(ref or rh).lstrip('#')}"
        meta = {"domain": "web-doc", "lang": "markdown", "ref": ref_tag, "no_read_file": True}
        changed = False
        if tool_name == "web_extract" and isinstance(data.get("results"), list):
            for item in data["results"]:
                if isinstance(item, dict) and isinstance(item.get("content"), str) and len(item["content"]) >= 400:
                    shaved, _ = shave_tiered(item["content"], meta)
                    if len(shaved) < len(item["content"]):
                        item["content"] = shaved
                        changed = True
        elif tool_name == "browser_exec":
            for key in ("content", "output", "result", "text"):
                val = data.get(key)
                if isinstance(val, str) and len(val) >= 1024 and "\n" in val:
                    shaved, _ = shave_tiered(val, meta)
                    if len(shaved) < len(val):
                        data[key] = shaved
                        changed = True
                        break
        if not changed or not ref:
            return None
        cand = _json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(cand) >= len(result):
            return None
        checked = _head_and_check(result, cand, ref)
        if checked is None:
            return None
        final, tb, ta = checked
        return final, _stats(result, final, tb, ta, "web_intake", rh, t0)
    if stripped.startswith(("{", "[")):
        return None
    ref = _ccr_put(result)
    if not ref:
        return None
    cand, w_stats = shave_tiered(
        result,
        {"domain": "web-doc", "lang": "markdown", "ref": f"#{ref.lstrip('#')}", "no_read_file": True},
    )
    if w_stats.get("saved_chars", 0) < 40 or len(cand) >= len(result):
        return None
    checked = _head_and_check(result, cand, ref)
    if checked is None:
        return None
    final, tb, ta = checked
    return final, _stats(result, final, tb, ta, "web_intake", rh, t0)


def _session_cache(cache_name: str, key: str, result: str, session_id: str) -> tuple[dict, dict] | None:
    """Read one session cache entry; returns (cache, previous_entry) or None."""
    from .dedup import _SEEN_LOCK, _seen

    with _SEEN_LOCK:
        cache = _seen(session_id or "default").setdefault(cache_name, {})
        return cache, cache.get(key)


def skill_cache(
    tool_name: str, args: Any, result: str, session_id: str, min_chars: int, t0: float
) -> tuple[str, dict] | None:
    """Repeat `skill_view` in one session -> 1-line pointer; cold view -> shaved prose."""
    from .ccr import put as _ccr_put
    from .strategies.tiered_shave import shave_tiered

    if tool_name != "skill_view" or not isinstance(args, dict):
        return None
    skill_name = str(args.get("name") or args.get("skill") or "")
    if not skill_name:
        return None
    rh = digest(result)
    entry = _session_cache("skills", skill_name, result, session_id)
    if entry is None:
        return None
    cache, prev = entry
    if prev and prev.get("digest") == rh:
        prev_ref = prev.get("ref", rh)
        stub = (
            f"[tt:skill_cached name=\"{skill_name}\" #{prev_ref} ({len(result)} chars) "
            "— identical to prior skill_view in active context -> trimtok_retrieve]"
        )
        tb, ta = _tok_pair(result, stub)
        return stub, _stats(result, stub, tb, ta, "skill_cache_hit", rh, t0)
    ref = _ccr_put(result)
    from .dedup import _SEEN_LOCK
    with _SEEN_LOCK:
        cache[skill_name] = {"digest": rh, "ref": ref or rh}
    if len(result) < min_chars:
        return None
    ref_tag = f"#{(ref or rh).lstrip('#')}"
    meta = {"domain": "spec-doc", "lang": "markdown", "ref": ref_tag, "no_read_file": True}
    cand = None
    saved = 0
    if result.strip().startswith("{") and '"content"' in result:
        try:
            parsed = _json.loads(result)
            if isinstance(parsed, dict) and isinstance(parsed.get("content"), str) and len(parsed["content"]) >= 2000:
                shaved, c_stats = shave_tiered(parsed["content"], meta)
                if c_stats.get("saved_chars", 0) > 200:
                    parsed["content"] = shaved
                    cand = _json.dumps(parsed, ensure_ascii=False)
                    saved = len(result) - len(cand)
        except Exception:
            pass
    elif result.strip().startswith(("#", "---")):
        try:
            cand, c_stats = shave_tiered(result, meta)
            saved = c_stats.get("saved_chars", 0)
        except Exception:
            pass
    if cand is None or saved <= 200 or len(cand) >= len(result):
        return None
    tb, ta = _tok_pair(result, cand)
    if ta >= tb:
        return None
    stats = _stats(result, cand, tb, ta, "skill_shaved", rh, t0)
    stats["fold_saved"] = saved
    stats["saved_chars"] = saved
    return cand, stats


def skills_list_cache(tool_name: str, args: Any, result: str, session_id: str, t0: float) -> tuple[str, dict] | None:
    """Repeat `skills_list` in one session -> 1-line pointer.

    The catalogue is identical for the whole session and was being re-sent up to
    10 times; nothing in it changes between calls.
    """
    from .ccr import put as _ccr_put

    if tool_name != "skills_list" or len(result) < 512 or has_marker_head(result):
        return None
    category = str(args.get("category") or "*") if isinstance(args, dict) else "*"
    rh = digest(result)
    entry = _session_cache("skills_list", category, result, session_id)
    if entry is None:
        return None
    cache, prev = entry
    if prev and prev.get("digest") == rh:
        prev_ref = prev.get("ref", rh)
        stub = (
            f"[tt:skills_list_cached category=\"{category}\" #{prev_ref} ({len(result)} chars) "
            "— identical to prior skills_list in active context -> trimtok_retrieve]"
        )
        tb, ta = _tok_pair(result, stub)
        return stub, _stats(result, stub, tb, ta, "skills_list_cache_hit", rh, t0)
    ref = _ccr_put(result)
    from .dedup import _SEEN_LOCK
    with _SEEN_LOCK:
        cache[category] = {"digest": rh, "ref": ref or rh}
    return None


def search_files_prefix(tool_name: str, result: str, t0: float) -> tuple[str, dict] | None:
    """Emit the shared directory prefix once for a `{"files": [...]}` result."""
    if tool_name != "search_files" or len(result) < 512 or not result.lstrip().startswith("{") or has_marker_head(result):
        return None
    try:
        data = _json.loads(result)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    files = data.get("files")
    if not isinstance(files, list) or len(files) < 4 or not all(isinstance(x, str) for x in files):
        return None
    norm = [x.replace("\\", "/") for x in files]
    dirs = [p.rsplit("/", 1)[0] + "/" for p in norm if "/" in p]
    if len(dirs) < 4:
        return None
    # Group by root signature so mixed Windows (C:/) and MSYS (/c/) paths don't break commonpath.
    buckets: dict[str, list[str]] = {}
    for d in dirs:
        buckets.setdefault(d[:3].lower(), []).append(d)
    majority = max(buckets.values(), key=len)
    if len(majority) < 3:
        return None
    raw_cp = os.path.commonprefix(majority)
    prefix = raw_cp[: raw_cp.rfind("/") + 1] if "/" in raw_cp else ""
    if len(prefix) < 12 or "@prefix" in data:
        return None
    rel = [nf[len(prefix):] if nf.startswith(prefix) else orig for nf, orig in zip(norm, files)]
    new_data = {"@prefix": prefix, **{k: (rel if k == "files" else v) for k, v in data.items()}}
    cand = _json.dumps(new_data, ensure_ascii=False, separators=(",", ":"))
    if len(cand) + 40 >= len(result):
        return None
    tb, ta = _tok_pair(result, cand)
    if ta >= tb:
        return None
    return cand, _stats(result, cand, tb, ta, "search_files_prefix", digest(result), t0)


def session_search_fold(tool_name: str, result: str, min_chars: int, t0: float) -> tuple[str, dict] | None:
    """Fold long bookends/messages/tool_calls, keeping the citations verbatim.

    `session_id`, `title`, `link`, `snippet` and `matched_role` are what the agent
    quotes back to the user, so they never move.
    """
    from .ccr import put as _ccr_put

    if tool_name != "session_search" or len(result) < min_chars or not result.lstrip().startswith("{") or has_marker_head(result):
        return None
    try:
        data = _json.loads(result)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list) or not data["results"]:
        return None
    rh = digest(result)
    ref = _ccr_put(result)
    if not ref:
        return None
    tag = f"#{ref.lstrip('#')}"
    changed = False

    def fold_mid(s: str, head_n: int = 280, tail_n: int = 140) -> str:
        nonlocal changed
        if len(s) < 800:
            return s
        changed = True
        return f"{s[:head_n]}\n[tt:folded {len(s) - head_n - tail_n} chars {tag} -> trimtok_retrieve]\n{s[-tail_n:]}"

    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        for key in ("bookend_start", "bookend_end"):
            if isinstance(item.get(key), str):
                item[key] = fold_mid(item[key])
        if not isinstance(item.get("messages"), list):
            continue
        for msg in item["messages"]:
            if not isinstance(msg, dict):
                continue
            if isinstance(msg.get("content"), str):
                msg["content"] = fold_mid(msg["content"])
            tool_calls = msg.get("tool_calls")
            if tool_calls and len(_json.dumps(tool_calls, ensure_ascii=False)) >= 400:
                names = []
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if isinstance(call, dict):
                            fn = (call.get("function") or {}).get("name") if isinstance(call.get("function"), dict) else call.get("name")
                            if fn:
                                names.append(fn)
                msg["tool_calls"] = [{"folded_tools": names or len(tool_calls), "ref": tag}]
                changed = True
    if not changed:
        return None
    cand = _json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(cand) >= len(result):
        return None
    checked = _head_and_check(result, cand, ref)
    if checked is None:
        return None
    final, tb, ta = checked
    return final, _stats(result, final, tb, ta, "session_search_folded", rh, t0)
