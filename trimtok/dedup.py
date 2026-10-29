"""trimtok Phase 2: tool-result compression (cross-turn span fold + hygiene + AST code skeletonization).

Stdlib only, pure functions + tiny module-level session corpus.
Never raises.
"""

from __future__ import annotations

import difflib
import hashlib
import json as _json
import os
import re
import threading
import time
from typing import Any

try:
    from .signals import (
        KEEP_PREFIX as _KEEP_PREFIX,
        LEGEND_TOON as _TOON_LEGEND,
        SENTINEL_RE,
        SLOT_RE as _SLOT_RE,
        adaptive_tool_head,
        estimate_bpe_gain,
        estimate_tokens,
        fast_json_loads,
        headtail_head,
        is_exempt,
        is_important,
        _VALUE_LINE_RE,
        masked,
        seen_line,
        seen_lines,
        shingle_head,
        template_head,
        tool_head,
    )
    from .pruners import fold_text_structure, group_search_hits, skeletonize_code, smart_skeletonize_code
    from .toon_codec import fold_json_to_toon
    from .ccr import put as _ccr_put
    from .strategies.lossless_min import minify_lossless
    from .strategies.tiered_shave import shave_tiered
except ImportError:
    from signals import (
        KEEP_PREFIX as _KEEP_PREFIX,
        LEGEND_TOON as _TOON_LEGEND,
        SENTINEL_RE,
        SLOT_RE as _SLOT_RE,
        adaptive_tool_head,
        estimate_bpe_gain,
        estimate_tokens,
        fast_json_loads,
        headtail_head,
        is_exempt,
        is_important,
        _VALUE_LINE_RE,
        masked,
        seen_line,
        seen_lines,
        shingle_head,
        template_head,
        tool_head,
    )
    from pruners import fold_text_structure, group_search_hits, skeletonize_code, smart_skeletonize_code
    from toon_codec import fold_json_to_toon
    from ccr import put as _ccr_put
    from strategies.lossless_min import minify_lossless
    from strategies.tiered_shave import shave_tiered

_WEB_TOOLS = frozenset({"web_extract", "web_search", "browser_exec"})

_MIN_SPAN_LINES = 5
_MIN_SPAN_CHARS = 200
_MIN_LINE_CHARS = 40  # single repeated lines at/above this fold individually
_MAX_SPANS = 200  # per-session corpus count cap (FIFO by insertion)
_MAX_SPAN_CHARS = 2 * 1024 * 1024  # retained source characters per session
_MAX_INDEXED_LINES = 20_000  # bound duplicate-line lookup keys per session

_WS_TRAIL_RE = re.compile(r"[ \t]+(?=\n|$)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
)
_SESS_RE = re.compile(r"\b20\d{6}_\d{6}_[0-9a-f]{4,}\b")
_TS_LONG_RE = re.compile(
    r"\b\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b"
)
_TS_SHORT_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])")

_SEEN: dict[str, dict] = {}
_SEEN_LOCK = threading.RLock()


def _seen(session_id: str) -> dict:
    key = session_id or "default"
    with _SEEN_LOCK:
        entry = _SEEN.get(key)
        if entry is None:
            entry = {
                "spans": [], "span_chars": 0, "lines": {}, "raw": set(), "turns": {}
            }
            _SEEN[key] = entry
        if "span_chars" not in entry:
            entry["span_chars"] = sum(len(text) for _, text in entry["spans"])
        if "turns" not in entry:
            entry["turns"] = {}
        return entry


_MASK_KEEP_TURNS = 2
_MASK_MIN_CHARS = 2000
_MASK_SUMMARY_LINES = 3


def note_turn(text: str, digest: str, session_id: str = "", turn_id: str = "") -> None:
    """Record a stored block's turn membership (mask clock)."""
    if not turn_id:
        return
    try:
        with _SEEN_LOCK:
            turns = _seen(session_id)["turns"]
            bucket = turns.get(turn_id)
            if bucket is None:
                bucket = {"hashes": [], "masked": False}
                turns[turn_id] = bucket
            if digest not in bucket["hashes"]:
                bucket["hashes"].append(digest)
    except Exception:
        pass


def expire_old_turns(
    session_id: str = "", keep_turns: int = _MASK_KEEP_TURNS
) -> int:
    """Drop fold-index entries (spans + lines) older than keep_turns turns."""
    try:
        with _SEEN_LOCK:
            seen = _seen(session_id)
            turns = seen.get("turns", {})
            ids = list(turns.keys())
            # File bodies stay relevant far longer than 2 tool steps: a loop of
            # read -> search -> terminal -> read must still recognise the file.
            file_keep = max(keep_turns * 4, _FILE_KEEP_TURNS)
            stale_file_hashes: set[str] = set()
            if len(ids) > file_keep:
                for tid in ids[: len(ids) - file_keep]:
                    stale_file_hashes.update(turns.get(tid, {}).get("hashes", []))
            if len(ids) <= keep_turns:
                return 0
            old_ids = ids[: len(ids) - keep_turns]
            doomed: set[str] = set()
            for tid in old_ids:
                doomed.update(turns.get(tid, {}).get("hashes", []))
                del turns[tid]
            if not doomed:
                return len(old_ids)
            kept_spans = []
            removed_chars = 0
            for span_hash, span_text in seen["spans"]:
                if span_hash in doomed:
                    removed_chars += len(span_text)
                else:
                    kept_spans.append((span_hash, span_text))
            seen["spans"] = kept_spans
            seen["span_chars"] = max(0, seen["span_chars"] - removed_chars)
            for line, owner in list(seen["lines"].items()):
                if owner in doomed:
                    del seen["lines"][line]
            # A cached file body whose turn expired must not be served later as
            # "identical to prior read in active context" -- the agent can no
            # longer see that body in its context window.
            for fmeta in seen.get("files", {}).values():
                if isinstance(fmeta, dict) and fmeta.get("digest") in stale_file_hashes:
                    fmeta["expired"] = True
            return len(old_ids)
    except Exception:
        return 0


def mask_summary(text: str, digest: str, saved_chars: int) -> str:
    """Placeholder for a masked old block. Model re-fetches via retrieve."""
    first = ""
    try:
        for ln in text.split("\n")[:_MASK_SUMMARY_LINES]:
            s = ln.strip()
            if s:
                first = s[:120]
                break
    except Exception:
        pass
    head = masked(saved_chars, digest)
    return head + (f"\nStarts with: {first}" if first else "")


def _drop_indexed_lines(seen: dict, owner: str) -> None:
    """Remove duplicate-line keys owned by an evicted span."""
    for line, line_owner in list(seen["lines"].items()):
        if line_owner == owner:
            del seen["lines"][line]


def _index_long_line(seen: dict, line: str, owner: str) -> None:
    """Add a unique long line while the per-session lookup budget allows."""
    if len(line) < _MIN_LINE_CHARS or line.startswith("[tt:"):
        return
    lines = seen["lines"]
    if len(lines) < _MAX_INDEXED_LINES:
        lines.setdefault(line, owner)


def hygiene(text: str) -> tuple[str, int]:
    """Trailing-ws strip + blank-run collapse. Returns (text, saved)."""
    new = text
    if text.endswith((" ", "\t")) or " \n" in text or "\t\n" in text:
        new = _WS_TRAIL_RE.sub("", new)
    if "\n\n\n" in new:
        new = _BLANK_RUN_RE.sub("\n\n", new)
    return new, len(text) - len(new)


def normalize_volatile(text: str) -> tuple[str, int]:
    """UUID/session-id/timestamp -> stable placeholders (cache-friendly)."""
    new = text
    if "-" in new:
        new = _UUID_RE.sub("<UUID>", new)
    if "20" in new and "_" in new:
        new = _SESS_RE.sub("<SESS>", new)
    if "-" in new or "/" in new:
        new = _TS_LONG_RE.sub("<TS>", new)
    if ":" in new:
        new = _TS_SHORT_RE.sub("<TS>", new)
    return new, len(text) - len(new)


def fold_spans(
    text: str,
    session_id: str = "",
    *,
    min_lines: int = _MIN_SPAN_LINES,
    min_chars: int = _MIN_SPAN_CHARS,
    owner: str | None = None,
    deadline: Any | None = None,
) -> tuple[str, list[tuple[str, str]], int]:
    """Replace verbatim runs seen in earlier tool results with pointers."""
    seen = _seen(session_id)
    corpus = seen["spans"]
    lines = text.split("\n")
    if not corpus:
        return text, [], 0
    by_first: dict[str, list[int]] = {}
    for idx, (h, span) in enumerate(corpus):
        fl = span.split("\n", 1)[0]
        by_first.setdefault(fl, []).append(idx)
    line_owner = seen["lines"]
    out: list[str] = []
    folded_chars = 0
    i = 0
    n = len(lines)
    new_spans: list[tuple[str, str]] = []
    while i < n:
        if deadline is not None and (i & 255) == 0:
            deadline.check()
        line = lines[i]
        if (
            line_owner.get(line) is not None
            and len(line) >= _MIN_LINE_CHARS
            and not line.startswith("[tt:")
            and not line.startswith(_KEEP_PREFIX)
            and not is_exempt(line)
            and not is_important(line)
        ):
            ptr = seen_line(owner or line_owner[line])
            out.append(ptr)
            folded_chars += len(line) - len(ptr)
            i += 1
            continue
        best: tuple[int, int, str] | None = None
        for idx in by_first.get(lines[i], ()):
            h, span = corpus[idx]
            slines = span.split("\n")
            m = 0
            while i + m < n and m < len(slines) and lines[i + m] == slines[m]:
                m += 1
            if m >= min_lines:
                seg = "\n".join(lines[i:i + m])
                if len(seg) >= min_chars and (best is None or m > best[0]):
                    best = (m, idx, h)
        if best:
            m, _, _h = best
            seg = "\n".join(lines[i:i + m])
            ptr = seen_lines(m, owner or _h)
            out.append(ptr)
            folded_chars += len(seg) - len(ptr)
            i += m
        else:
            folded_line, extra = _fold_inner(lines[i], line_owner, owner)
            out.append(folded_line)
            folded_chars += extra
            i += 1
    if folded_chars == 0 and out:
        corpus_long = [ln for ln in line_owner if len(ln.split()) >= _SHINGLE_MIN_WORDS]
        if corpus_long:
            out, sh_saved = _fold_shingles(out, corpus_long, owner, deadline)
            folded_chars += sh_saved
    return "\n".join(out), new_spans, folded_chars


def _fold_intra_repeats(text: str, deadline: Any | None = None) -> tuple[str, int]:
    """Fold adjacent duplicate line runs inside one first-seen document."""
    lines = text.split("\n")
    if len(lines) < 4:
        return text, 0
    out: list[str] = []
    saved = 0
    i = 0
    n = len(lines)
    while i < n:
        if deadline is not None and (i & 511) == 0:
            deadline.check()
        line = lines[i]
        key = line.strip()
        if (
            not key
            or len(key) < _MIN_LINE_CHARS
            or line.startswith("[tt:")
            or line.startswith(_KEEP_PREFIX)
        ):
            out.append(line)
            i += 1
            continue
        j = i + 1
        while j < n and lines[j].strip() == key:
            j += 1
        run = j - i
        if run >= 3:
            out.append(line)
            marker = f"[repeated {run - 1}x]"
            out.append(marker)
            saved += sum(len(lines[k]) for k in range(i + 1, j)) - len(marker)
        else:
            out.extend(lines[i:j])
        i = j
    if not saved:
        return text, 0
    return "\n".join(out), saved


def _intra_template_key(line: str) -> str:
    norm = line.strip()
    if len(norm) < 20:
        return ""
    # Distinct port/config values must not collapse into one template key.
    if _VALUE_LINE_RE.search(line or ""):
        return ""
    key = _SLOT_RE.sub("{}", norm)
    if len(key) < 20:
        return ""
    if "{}" not in key:
        return ""
    return key


def _fold_intra_templates(
    text: str, owner: str | None = None, deadline: Any | None = None
) -> tuple[str, int]:
    lines = text.split("\n")
    groups: dict[str, list[int]] = {}
    for idx, line in enumerate(lines):
        if deadline is not None and (idx & 511) == 0:
            deadline.check()
        if (
            not line.strip()
            or line.startswith("[tt:")
            or line.startswith(_KEEP_PREFIX)
            or is_important(line)
        ):
            continue
        key = _intra_template_key(line)
        if key:
            groups.setdefault(key, []).append(idx)
    big = {k: v for k, v in groups.items() if len(v) >= 3}
    if not big:
        return text, 0
    first_of = {}
    for key, members in big.items():
        first_of[members[0]] = (key, len(members))
    in_big = set()
    for members in big.values():
        in_big.update(members[1:])
    out: list[str] = []
    saved = 0
    for idx, line in enumerate(lines):
        if idx in in_big:
            saved += len(line)
            continue
        out.append(line)
        if idx in first_of:
            _, count = first_of[idx]
            marker = template_head(count - 1, owner or "")
            out.append(marker)
            saved -= len(marker)
    if saved <= 0:
        return text, 0
    return "\n".join(out), saved


_HT_MIN_CHARS = 100_000
_HT_HEAD_LINES = 30
_HT_TAIL_LINES = 30


def _fold_headtail(text: str) -> tuple[str, int]:
    """HEAD_TAIL stub for large non-repetitive docs."""
    if len(text) < _HT_MIN_CHARS or "[tt:" in text[:2000]:
        return text, 0
    lines = text.split("\n")
    if len(lines) <= _HT_HEAD_LINES + _HT_TAIL_LINES + 10:
        return text, 0
    head = lines[:_HT_HEAD_LINES]
    tail = lines[-_HT_TAIL_LINES:]
    omitted = len(lines) - len(head) - len(tail)
    stub = f"[... {omitted} lines in CCR ...]"
    new_text = "\n".join(head + [stub] + tail)
    saved = len(text) - len(new_text)
    if saved <= 0:
        return text, 0
    return new_text, saved


def _shingles(line: str) -> frozenset:
    words = line.strip().split()
    if len(words) < 4:
        return frozenset()
    return frozenset(tuple(words[i:i + 3]) for i in range(len(words) - 2))


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


_SHINGLE_MIN_JACCARD = 0.8
_SHINGLE_MIN_WORDS = 6


def _fold_shingles(
    lines: list[str],
    corpus_lines: list[str],
    owner: str | None,
    deadline: Any | None = None,
) -> tuple[list[str], int]:
    """Fold near-duplicate lines (Jaccard>=0.8) against prior corpus lines."""
    if not corpus_lines:
        return lines, 0
    prints: list[dict] = []
    for cl in corpus_lines:
        if len(cl.split()) >= _SHINGLE_MIN_WORDS and not cl.startswith("[tt:"):
            prints.append((cl, _shingles(cl)))
    if not prints:
        return lines, 0
    min_len = len(shingle_head(1, "0" * 12))
    out: list[str] = []
    saved = 0
    for li, line in enumerate(lines):
        if deadline is not None and (li & 255) == 0:
            deadline.check()
        if (
            len(line) <= min_len
            or line.startswith("[tt:")
            or line.startswith(_KEEP_PREFIX)
            or is_exempt(line)
            or is_important(line)
        ):
            out.append(line)
            continue
        fp = _shingles(line)
        hit = False
        for cl, cfp in prints:
            if cl == line:
                continue
            if _jaccard(fp, cfp) >= _SHINGLE_MIN_JACCARD:
                out.append(shingle_head(1, owner or ""))
                saved += max(0, len(line) - len(out[-1]))
                hit = True
                break
        if not hit:
            out.append(line)
    return out, saved


def _fold_inner(
    line: str, line_owner: dict[str, str], owner: str | None = None
) -> tuple[str, int]:
    """Fold indexed inner lines inside one JSON envelope line."""
    if len(line) < 500 or line.lstrip()[:1] not in "{[":
        return line, 0
    try:
        data = fast_json_loads(line)
    except Exception:
        return line, 0
    saved = 0

    def _walk(o: object) -> object:
        nonlocal saved
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(v) for v in o]
        if isinstance(o, str) and "\n" in o:
            parts = o.split("\n")
            kept = 0
            for idx, ln in enumerate(parts):
                if (
                    line_owner.get(ln) is not None
                    and len(ln) >= _MIN_LINE_CHARS
                    and not ln.startswith("[tt:")
                ):
                    ptr = seen_line(owner or line_owner[ln])
                    saved += len(ln) - len(ptr)
                    parts[idx] = ptr
                    kept += 1
            if kept:
                return "\n".join(parts)
        return o

    try:
        new_data = _walk(data)
        if not saved:
            return line, 0
        return _json.dumps(new_data, separators=(",", ":"), ensure_ascii=False), saved
    except Exception:
        return line, 0


def _index_json_inners(seen: dict, text: str, key: str) -> None:
    """Index lines inside JSON string fields."""
    import json as _json

    def _walk(o: object) -> None:
        if isinstance(o, dict):
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
        elif isinstance(o, str):
            for ln in o.split("\n"):
                _index_long_line(seen, ln, key)

    if text.lstrip()[:1] in "{[":
        try:
            _walk(fast_json_loads(text))
            return
        except Exception:
            pass
        try:
            obj, _ = _json.JSONDecoder().raw_decode(text.strip())
            _walk(obj)
            return
        except Exception:
            pass
    for ln in text.split("\n"):
        s = ln.strip()
        if len(s) > 200 and s[:1] in "{[":
            try:
                _walk(fast_json_loads(s))
            except Exception:
                continue


def remember(text: str, session_id: str = "", owner: str | None = None) -> str:
    """Index content for future folding within bounded per-session budgets."""
    if not isinstance(text, str):
        return owner or ""
    key = owner or hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    if len(text) > _MAX_SPAN_CHARS:
        return key
    with _SEEN_LOCK:
        seen = _seen(session_id)
        corpus = seen["spans"]
        if any(eh == key for eh, _ in corpus):
            return key
        while corpus and (
            len(corpus) >= _MAX_SPANS
            or seen["span_chars"] + len(text) > _MAX_SPAN_CHARS
        ):
            old_hash, old_text = corpus.pop(0)
            seen["span_chars"] = max(0, seen["span_chars"] - len(old_text))
            _drop_indexed_lines(seen, old_hash)
        corpus.append((key, text))
        seen["span_chars"] += len(text)
        for ln in text.split("\n"):
            _index_long_line(seen, ln, key)
        _index_json_inners(seen, text, key)
    return key


def clear_session(session_id: str = "") -> None:
    with _SEEN_LOCK:
        _SEEN.pop(session_id or "default", None)


def mask_old_turns(
    session_id: str = "",
    current_turn: str = "",
    *,
    keep_turns: int = _MASK_KEEP_TURNS,
    store: dict[str, str] | None = None,
) -> dict[str, str]:
    """Replace stored originals older than keep_turns with placeholders."""
    out: dict[str, str] = {}
    try:
        with _SEEN_LOCK:
            seen = _seen(session_id)
            turns = seen.get("turns", {})
            ids = list(turns.keys())
            if current_turn in ids:
                cutoff = ids.index(current_turn) - keep_turns
                old_ids = ids[: cutoff + 1] if cutoff >= 0 else []
            else:
                old_ids = ids[:-keep_turns] if len(ids) > keep_turns else []
            for tid in old_ids:
                bucket = turns.get(tid, {})
                if bucket.get("masked"):
                    continue
                for digest in bucket.get("hashes", []):
                    original = (store or {}).get(digest, "")
                    if not original or len(original) < _MASK_MIN_CHARS:
                        continue
                    out[digest] = mask_summary(original, digest, len(original))
                bucket["masked"] = True
    except Exception:
        pass
    return out


def minify_json(text: str) -> tuple[str, int]:
    """Lossless JSON minify (whole-doc only, never partial). Returns (text, saved)."""
    try:
        import json as _json

        stripped = text.strip()
        if not stripped or stripped[0] not in "{[":
            return text, 0
        try:
            data = fast_json_loads(stripped)
            tail = ""
        except Exception:
            try:
                data, idx = _json.JSONDecoder().raw_decode(stripped)
            except Exception:
                return text, 0
            tail = stripped[idx:]
            if len(tail) > 4096 or not tail.strip():
                return text, 0
            if "{" in tail or "[" in tail:
                return text, 0
        mini = _json.dumps(data, separators=(",", ":"), ensure_ascii=False) + tail
        if len(mini) < len(text):
            return mini, len(text) - len(mini)
        return text, 0
    except Exception:
        return text, 0


# ---------------------------------------------------------------------------
# 5. v4.2: Stateful Read-File Engine, Micro-Minify, raw_hashes fix
# ---------------------------------------------------------------------------
_MICRO_MIN_CHARS = 512
_COLD_READ_SKELETON_MIN_CHARS = 12_000  # files <= 12KB: cold read stays verbatim (patch safety)
_NON_CODE_MIN_CHARS = 2_000             # non-code docs/configs have no patch line offset constraints
# Prose/config files get the structural fold. Code extensions are deliberately
# absent: folding a .py file with no long functions would break patch-by-offset.
_TEXT_STRUCT_EXTS = frozenset({
    "md", "markdown", "rst", "txt", "yaml", "yml", "toml", "ini", "cfg", "jsonl", "log", "csv",
})
_READ_DELTA_CONTEXT_LINES = 12          # real lines shown +/- 12 around an edited region
_MAX_FILE_CACHE = 64                    # per-session cached file bodies (memory bound)
_MUTATING_TOOLS = frozenset({
    "patch", "edit_file", "write_file", "replace_in_file",
    "multi_edit", "apply_patch", "create_file", "str_replace_editor",
})
# Hermes exposes a second read tool with the same payload shape.
_READ_FILE_TOOLS = frozenset({
    "read_file", "read_file_extra", "view_file", "get_file",
})
_DEFAULT_LIMIT_FLOOR = 500   # Hermes default limit=2000; an explicit slice <500 is targeted
_FILE_KEEP_TURNS = 8         # file bodies stay relevant far longer than 2 tool steps
_NUMBERED_LINE_RE = re.compile(r"^(\s*(\d+)\|)(.*)$")

# Shell commands that can change files on disk -> the cached file body is stale.
_MUTATING_SHELL_RE = re.compile(
    r"(?i)(?:\bgit\s+(?:checkout|reset|restore|switch|clean|stash\s+(?:pop|apply|drop))\b"
    r"|\bsed\s+-i\b|\btruncate\b|\btee\b|\b(?:cp|mv|rm)\s+-\w|\bcat\b[^|;]*>)"
)


def invalidate_shell_mutations(command: str, session_id: str = "") -> int:
    """Drop cached file bodies when a shell command may have changed files.

    Conservative by design: the whole per-session file cache is dropped, so the
    next read is a cold verbatim read.  That is a small token cost, never a
    correctness risk.
    """
    if not command or not _MUTATING_SHELL_RE.search(command):
        return 0
    try:
        with _SEEN_LOCK:
            seen = _seen(session_id or "default")
            files = seen.get("files") or {}
            n = len(files)
            if n:
                seen["files"] = {}
            return n
    except Exception:
        return 0


def _extract_file_args(args: Any) -> tuple[str, int | None, int | None]:
    """Extract normalized (file_path, offset, limit) from tool arguments."""
    if isinstance(args, str) and args.strip().startswith("{"):
        try:
            args = fast_json_loads(args)
        except Exception:
            return "", None, None
    if not isinstance(args, dict):
        return "", None, None
    raw_path = (
        args.get("path")
        or args.get("file_path")
        or args.get("target_file")
        or args.get("filename")
        or ""
    )
    norm_path = os.path.normcase(os.path.normpath(str(raw_path).strip())) if raw_path else ""
    offset = args.get("offset") or args.get("start_line")
    limit = args.get("limit") or args.get("num_lines") or args.get("end_line")
    try:
        offset_i = int(offset) if offset is not None else None
    except Exception:
        offset_i = None
    try:
        limit_i = int(limit) if limit is not None else None
    except Exception:
        limit_i = None
    return norm_path, offset_i, limit_i


def _unwrap_numbered_lines(
    text: str,
) -> tuple[str, list[str] | None, list[int] | None, list[str], list[str]]:
    """Unwrap Hermes `LINE|CONTENT` output into (raw_body, prefixes, line_nos, headers, footers).

    Hermes' read_file returns every line prefixed with its own line number
    ("42|    return total"). That prefix breaks ast.parse(), breaks the outline
    detector and makes every re-read look like a full-file change, so the 3-tier
    engine has to look at the raw source and re-attach the original numbers.
    Raw (unnumbered) payloads are returned untouched.
    """
    lines = text.split("\n")
    if len(lines) < 4:
        return text, None, None, [], []
    sample = [ln for ln in lines[:12] if ln.strip()]
    matches = sum(1 for ln in sample if _NUMBERED_LINE_RE.match(ln))
    if matches < max(2, len(sample) // 2):
        return text, None, None, [], []
    first_idx = -1
    last_idx = -1
    for i, ln in enumerate(lines):
        if _NUMBERED_LINE_RE.match(ln):
            if first_idx == -1:
                first_idx = i
            last_idx = i
    if first_idx == -1 or last_idx - first_idx < 3:
        return text, None, None, [], []
    headers = lines[:first_idx]
    footers = lines[last_idx + 1:]
    prefixes: list[str] = []
    line_nos: list[int] = []
    raw_lines: list[str] = []
    for i in range(first_idx, last_idx + 1):
        m = _NUMBERED_LINE_RE.match(lines[i])
        if m:
            prefixes.append(m.group(1))
            line_nos.append(int(m.group(2)))
            raw_lines.append(m.group(3))
        else:
            prev_no = line_nos[-1] if line_nos else 1
            prefixes.append("")
            line_nos.append(prev_no)
            raw_lines.append(lines[i])
    return "\n".join(raw_lines), prefixes, line_nos, headers, footers


def _is_targeted_slice(
    offset: int | None,
    limit: int | None,
    first_line_no: int,
    n_lines: int,
) -> bool:
    """Is this a targeted line-range slice (must stay verbatim) or a full read?

    Hermes always fills `offset=1, limit=2000` on a full read, so the mere
    presence of those args says nothing. A read is targeted only when it starts
    inside the file or when the caller deliberately capped a small window.
    """
    if offset is not None and offset > 1:
        return True
    if first_line_no > 1:
        return True
    if limit is not None and limit < _DEFAULT_LIMIT_FLOOR and n_lines >= limit:
        return True
    return False


def _build_file_outline(text: str, line_nos: list[int] | None = None, max_items: int = 35) -> str:
    """Fast O(N) structural outline (classes/functions with real file line numbers)."""
    items: list[str] = []
    raw_lines = text.split("\n")
    for idx, line in enumerate(raw_lines):
        ln_no = line_nos[idx] if (line_nos and idx < len(line_nos)) else (idx + 1)
        s = line.lstrip()
        if s.startswith(("def ", "async def ", "class ", "interface ", "func ", "fn ", "pub fn ", "export function ")):
            sig = s.split("{")[0].strip()
            if sig.endswith(":"):
                sig = sig[:-1].strip()
            items.append(f"  L{ln_no}: {sig[:90]}")
            if len(items) >= max_items:
                items.append("  ...")
                break
    return "\n".join(items) if items else f"  (total {len(raw_lines)} lines)"


def _build_read_delta(
    prev_text: str,
    cur_text: str,
    cur_ref: str,
    prev_ref: str,
    *,
    cur_prefixes: list[str] | None = None,
    cur_line_nos: list[int] | None = None,
    ctx_lines: int = _READ_DELTA_CONTEXT_LINES,
) -> tuple[str, int]:
    """Build a line-numbered contextual diff view of a modified file.

    `prev_text` / `cur_text` are the UNPREFIXED sources; the original Hermes
    `LINE|` prefixes are re-attached to the emitted lines so the agent can still
    patch by real line number.
    """
    prev_lines = prev_text.split("\n")
    cur_lines = cur_text.split("\n")
    matcher = difflib.SequenceMatcher(None, prev_lines, cur_lines, autojunk=False)
    changed_cur_indices: list[int] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed_cur_indices.extend(range(j1, max(j1 + 1, j2)))

    if not changed_cur_indices:
        return cur_text, 0

    total = len(cur_lines)
    intervals: list[tuple[int, int]] = []
    for idx in changed_cur_indices:
        s = max(0, idx - ctx_lines)
        e = min(total, idx + ctx_lines + 1)
        if intervals and s <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], e))
        else:
            intervals.append((s, e))

    chunks = [
        f"[tt:file_delta #{cur_ref} vs #{prev_ref} | {total}L total, showing modified hunks +/-{ctx_lines}L]"
    ]
    prev_end = 0
    for s, e in intervals:
        if s > prev_end:
            l_start = cur_line_nos[prev_end] if (cur_line_nos and prev_end < len(cur_line_nos)) else (prev_end + 1)
            l_end = cur_line_nos[s - 1] if (cur_line_nos and s - 1 < len(cur_line_nos)) else s
            chunks.append(f"[... L{l_start}-L{l_end} ({s - prev_end} unchanged lines in #{cur_ref}) ...]")
        for ln_idx in range(s, e):
            prefix = cur_prefixes[ln_idx] if (cur_prefixes and ln_idx < len(cur_prefixes)) else f"{ln_idx + 1}|"
            chunks.append(f"{prefix}{cur_lines[ln_idx]}")
        prev_end = e
    if prev_end < total:
        l_start = cur_line_nos[prev_end] if (cur_line_nos and prev_end < len(cur_line_nos)) else (prev_end + 1)
        l_end = cur_line_nos[total - 1] if (cur_line_nos and total - 1 < len(cur_line_nos)) else total
        chunks.append(f"[... L{l_start}-L{l_end} ({total - prev_end} unchanged lines in #{cur_ref}) ...]")

    delta_str = "\n".join(chunks)
    orig_approx_len = len(cur_text) + (len(cur_lines) * 5 if cur_prefixes else 0)
    saved = orig_approx_len - len(delta_str)
    if saved <= 120:
        return cur_text, 0
    return delta_str, saved


def _tok_pair(orig: str, cand: str) -> tuple[int, int]:
    """Token counts for the BPE non-inflation gate.

    A real BPE pass costs ~0.5 ms per KB, which dominates a file read. When the
    candidate is clearly shorter (<90% of the original) the char ratio already
    proves the token count dropped, so the expensive pass is only used for the
    near-equal case where it actually matters.
    """
    if len(cand) < len(orig) * 0.9:
        return len(orig) // 4, len(cand) // 4
    return estimate_tokens(orig), estimate_tokens(cand)


def _handle_read_file_v42(
    result: str,
    session_id: str,
    args: Any,
    min_chars: int,
    t0: float,
    deadline: Any | None = None,
) -> tuple[str, dict]:
    """3-Tier Stateful Handler for read_file / read_file_extra with 100% Patch Safety.

    Hermes hands us `LINE|CONTENT` payloads and always fills offset=1/limit=2000,
    so the payload is unwrapped to raw source first and the original line
    numbers are re-attached to whatever we emit.
    """
    norm_path, offset, limit = _extract_file_args(args)
    digest = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
    raw_body, prefixes, line_nos, headers, footers = _unwrap_numbered_lines(result)
    clean_text, _ = hygiene(raw_body)
    remember(clean_text, session_id, owner=digest)

    first_line_no = line_nos[0] if line_nos else (offset if offset and offset > 0 else 1)
    n_lines = len(line_nos) if line_nos else (raw_body.count("\n") + 1)

    # Rule 0: a targeted slice (or a small file) stays verbatim so that a
    # subsequent patch is never handed a mangled body.
    if len(result) < min_chars or _is_targeted_slice(offset, limit, first_line_no, n_lines):
        return result, {
            "gate": "passthrough_reference", "digest": digest,
            "reason": "small" if len(result) < min_chars else "targeted_slice",
            "ms": (time.perf_counter() - t0) * 1000,
        }

    with _SEEN_LOCK:
        seen = _seen(session_id)
        file_cache: dict[str, dict] = seen.setdefault("files", {})
        cache_key = norm_path or f"__anon_{digest}"
        prev_entry = file_cache.get(cache_key)

        # Case A: the same file is read again with identical content.
        if (
            prev_entry is not None
            and prev_entry.get("digest") == digest
            and not prev_entry.get("expired", False)
        ):
            # Anti-poisoning rail: if the agent already got a stub (or only a
            # skeleton) for this file, hand over the real body instead of
            # another pointer -- otherwise it loops on stubs.
            if prev_entry.get("skeleton_only") or prev_entry.get("stub_hits", 0) >= 1:
                prev_entry["skeleton_only"] = False
                prev_entry["stub_hits"] = 0
                return result, {
                    "gate": "passthrough_reference", "digest": digest,
                    "reason": "anti_poison",
                    "ms": (time.perf_counter() - t0) * 1000,
                }
            prev_entry["reads"] = prev_entry.get("reads", 1) + 1
            prev_entry["stub_hits"] = prev_entry.get("stub_hits", 0) + 1
            outline = _build_file_outline(raw_body, line_nos=line_nos)
            stub = (
                f"[tt:file_unchanged #{digest} path=\"{norm_path or 'file'}\" "
                f"({len(result)} chars, {n_lines}L) — identical to prior read in active context]\n"
                f"Outline (read_file offset/limit for exact lines):\n"
                f"{outline}"
            )
            tb, ta = _tok_pair(result, stub)
            if len(stub) < len(result) and ta < tb:
                return stub, {
                    "gate": "ok",
                    "kind": "file_cache_hit",
                    "digest": digest,
                    "before_chars": len(result),
                    "after_chars": len(stub),
                    "saved_chars": len(result) - len(stub),
                    "est_tokens_before": tb,
                    "est_tokens_after": ta,
                    "fold_saved": len(result) - len(stub),
                    "ms": (time.perf_counter() - t0) * 1000,
                }

        # Case B: the file was modified since the last read -> contextual delta.
        if norm_path and prev_entry is not None and prev_entry.get("digest") != digest:
            prev_raw = prev_entry.get("raw_body") or ""
            prev_full = prev_entry.get("text", prev_raw)
            prev_digest = prev_entry.get("digest", "")
            # SequenceMatcher is O(n*m) on lines -- skip the delta for big files.
            if prev_raw and n_lines <= 1500:
                # The header advertises #prev_ref, so the previous body has to be
                # retrievable by that ref -> store it in the CCR now.
                prev_ref = prev_digest
                try:
                    stored = _ccr_put(prev_full)
                    if stored:
                        prev_ref = stored.lstrip("#")
                except Exception:
                    pass
                delta_view, _ = _build_read_delta(
                    prev_raw, raw_body, digest, prev_ref,
                    cur_prefixes=prefixes, cur_line_nos=line_nos,
                )
                d_saved = len(result) - len(delta_view)
                file_cache[cache_key] = {
                    "digest": digest, "text": result, "raw_body": raw_body,
                    "reads": 1, "stub_hits": 0, "expired": False, "skeleton_only": False,
                }
                if d_saved > 120:
                    tb, ta = _tok_pair(result, delta_view)
                    if ta < tb:
                        return delta_view, {
                            "gate": "ok",
                            "kind": "file_delta",
                            "digest": digest,
                            "before_chars": len(result),
                            "after_chars": len(delta_view),
                            "saved_chars": d_saved,
                            "est_tokens_before": tb,
                            "est_tokens_after": ta,
                            "fold_saved": d_saved,
                            "ms": (time.perf_counter() - t0) * 1000,
                        }

        # Record the state of this read (first read or a read we did not fold).
        if len(file_cache) >= _MAX_FILE_CACHE:
            for _old_key in list(file_cache.keys())[: len(file_cache) - _MAX_FILE_CACHE + 1]:
                file_cache.pop(_old_key, None)
        file_cache[cache_key] = {
            "digest": digest, "text": result, "raw_body": raw_body,
            "reads": 1, "stub_hits": 0, "expired": False, "skeleton_only": False,
        }

        # Case C: cold first read of a large file -> line-anchored AST skeleton.
        # Fail-open: if the guard budget is already gone, ship the verbatim file
        # instead of blowing the deadline.
        budget_left = True
        if deadline is not None:
            try:
                deadline.check()
            except Exception:
                budget_left = False
        _ext = (norm_path or "").rsplit(".", 1)[-1].lower() if "." in (norm_path or "") else ""
        min_skel = _NON_CODE_MIN_CHARS if _ext in _TEXT_STRUCT_EXTS else _COLD_READ_SKELETON_MIN_CHARS
        if budget_left and (len(result) >= min_skel or len(raw_body) >= min_skel):
            # The skeleton advertises retrieve(hash=#...), so the full body must be in
            # the CCR before the marker ships -- otherwise the folded 60% of the file
            # is unreachable (dead pointer). No store -> stay verbatim.
            _sk_ref = _ccr_put(result)
            if not _sk_ref:
                return result, {
                    "gate": "passthrough_reference", "digest": digest,
                    "reason": "ccr_unavailable",
                    "ms": (time.perf_counter() - t0) * 1000,
                }
            sk_body, sk_saved, fn_count = smart_skeletonize_code(
                raw_body, ref=f"#{_sk_ref.lstrip('#')}", line_prefixes=prefixes, line_nos=line_nos,
            )
            sk_kind = "file_skeleton"
            _ext = (norm_path or "").rsplit(".", 1)[-1].lower() if "." in (norm_path or "") else ""
            if not (sk_saved > 200 and fn_count > 0) and _ext in _TEXT_STRUCT_EXTS:
                # Not Python (markdown/yaml/rst/jsonl): the AST lane has nothing to
                # say, so fold prose instead of shipping the whole file verbatim.
                sk_body, sk_saved = fold_text_structure(
                    raw_body, ref=f"#{_sk_ref.lstrip('#')}", line_prefixes=prefixes, line_nos=line_nos,
                )
                fn_count = 1 if sk_saved > 200 else 0
                sk_kind = "file_structure"
            if sk_saved > 200 and fn_count > 0:
                rebuilt = "\n".join(headers + [sk_body] + footers) if (headers or footers) else sk_body
                actual_saved = len(result) - len(rebuilt)
                head = adaptive_tool_head(max(actual_saved, sk_saved), digest, len(result))
                candidate = f"{head}\n{rebuilt}" if head else rebuilt
                tb, ta = _tok_pair(result, candidate)
                if len(candidate) < len(result) and ta < tb:
                    file_cache[cache_key]["skeleton_only"] = True
                    return candidate, {
                        "gate": "ok",
                        "kind": sk_kind,
                        "digest": digest,
                        "before_chars": len(result),
                        "after_chars": len(candidate),
                        "saved_chars": len(result) - len(candidate),
                        "est_tokens_before": tb,
                        "est_tokens_after": ta,
                        "skeleton_saved": len(result) - len(candidate),
                        "ms": (time.perf_counter() - t0) * 1000,
                    }

    # Cold first read of a file under _COLD_READ_SKELETON_MIN_CHARS:
    # If not a targeted slice, try safe cosmetic cleanup (clean license/banners)
    if not _is_targeted_slice(offset, limit, first_line_no, n_lines):
        _min_res, _min_stats = minify_lossless(result)
        if _min_stats.get("saved_chars", 0) >= 40 and len(_min_res) < len(result) and not SENTINEL_RE.search(result[:2000]):
            tb, ta = _tok_pair(result, _min_res)
            if ta < tb:
                return _min_res, {
                    "gate": "ok",
                    "kind": "file_clean",
                    "digest": digest,
                    "before_chars": len(result),
                    "after_chars": len(_min_res),
                    "saved_chars": len(result) - len(_min_res),
                    "est_tokens_before": tb,
                    "est_tokens_after": ta,
                    "ms": (time.perf_counter() - t0) * 1000,
                }

    return result, {
        "gate": "passthrough_reference", "digest": digest,
        "reason": "below_skeleton_min",
        "ms": (time.perf_counter() - t0) * 1000,
    }


def micro_minify_tool(result: str, session_id: str, t0: float) -> tuple[str, dict]:
    """Zero-risk fast lane for 512..2047 char tool outputs (Tier 0 headerless).

    Deliberately avoids estimate_tokens(): that is a real BPE pass (~0.19 ms per
    1.6 KB) and this lane's whole point is to be near-free on every tool result
    (plan target p95 < 0.25 ms). hygiene/minify_json only remove characters, so a
    char-based estimate is both safe and honest here.
    """
    digest = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
    # A unified diff carries " " on its blank context lines -- hygiene would strip
    # that space and corrupt the diff.
    if "diff --git" in result[:512] or "\n@@ -" in result[:512]:
        text, h_saved = result, 0
    else:
        text, h_saved = hygiene(result)
    text, m_saved = minify_json(text)
    total_saved = len(result) - len(text)
    if total_saved >= 4 and len(text) < len(result):
        remember(text, session_id, owner=digest)
        return text, {
            "gate": "ok",
            "digest": digest,
            "lossless_tier0": True,  # nothing semantic was removed -> no CCR write needed
            "before_chars": len(result),
            "after_chars": len(text),
            "saved_chars": total_saved,
            "est_tokens_before": len(result) // 4,
            "est_tokens_after": len(text) // 4,
            "hygiene_saved": h_saved,
            "minify_saved": m_saved,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    return result, {"gate": "passthrough_small", "reason": "micro_no_gain",
                     "ms": (time.perf_counter() - t0) * 1000}


def compress_tool_result(
    tool_name: str,
    result: str,
    *,
    args: Any = None,
    session_id: str = "",
    min_chars: int = 2048,
    toon_enabled: bool = True,
    deadline: Any | None = None,
) -> tuple[str, dict]:
    """Full v2+v4 pipeline on one tool result (scoped search + TOON + AST code skeletonizer). Never raises."""
    t0 = time.perf_counter()
    try:
        if not isinstance(result, str):
            return result, {"gate": "passthrough_non_str", "ms": 0.0}
        if SENTINEL_RE.search(result[:2000]):
            return result, {"gate": "passthrough_sentinel", "reason": "sentinel", "ms": 0.0}
        # A mutating tool changes the file, but the cached body is what the next
        # read is diffed against -- dropping it here meant the file_delta lane
        # could never fire in a real read -> patch -> re-read loop.
        if tool_name in _MUTATING_TOOLS:
            try:
                mut_path, _, _ = _extract_file_args(args)
                if mut_path:
                    with _SEEN_LOCK:
                        f_entry = _seen(session_id or "default").setdefault("files", {}).get(mut_path)
                        if isinstance(f_entry, dict):
                            f_entry["mutated"] = True
                            f_entry["stub_hits"] = 0
            except Exception:
                pass
        if tool_name in _READ_FILE_TOOLS:
            return _handle_read_file_v42(result, session_id, args, min_chars, t0, deadline)
        if tool_name in _WEB_TOOLS:
            _rh = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
            w_cand, w_stats = shave_tiered(result, {"domain": "web-doc", "lang": "markdown"})
            if w_stats.get("saved_chars", 0) >= 40 and len(w_cand) < len(result) and not SENTINEL_RE.search(result[:2000]):
                tb, ta = _tok_pair(result, w_cand)
                if ta < tb:
                    _ref = _ccr_put(result)
                    head = adaptive_tool_head(len(result) - len(w_cand), _rh, len(result), is_lossless_only=False)
                    final_w = f"{head}\n{w_cand}" if head else w_cand
                    return final_w, {
                        "gate": "ok",
                        "kind": "web_intake",
                        "digest": _rh,
                        "before_chars": len(result),
                        "after_chars": len(final_w),
                        "saved_chars": len(result) - len(final_w),
                        "est_tokens_before": tb,
                        "est_tokens_after": ta,
                        "ms": (time.perf_counter() - t0) * 1000,
                    }
        if tool_name == "skill_view" and isinstance(args, dict):
            # Repeated skill reads in the same session collapse to a 1-line cache hit pointer
            skill_name = str(args.get("name") or args.get("skill") or "")
            if skill_name:
                _rh = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
                with _SEEN_LOCK:
                    s_seen = _seen(session_id or "default")
                    s_cache = s_seen.setdefault("skills", {})
                    prev_skill = s_cache.get(skill_name)
                    if prev_skill and prev_skill.get("digest") == _rh:
                        prev_ref = prev_skill.get("ref", _rh)
                        stub = f"[tt:skill_cached name=\"{skill_name}\" #{prev_ref} ({len(result)} chars) — identical to prior skill_view in active context -> trimtok_retrieve]"
                        tb, ta = _tok_pair(result, stub)
                        return stub, {
                            "gate": "ok", "kind": "skill_cache_hit", "digest": _rh,
                            "before_chars": len(result), "after_chars": len(stub),
                            "saved_chars": len(result) - len(stub),
                            "est_tokens_before": tb, "est_tokens_after": ta,
                            "ms": (time.perf_counter() - t0) * 1000,
                        }
                    ref = _ccr_put(result)
                    s_cache[skill_name] = {"digest": _rh, "ref": ref or _rh}
        if len(result) < min_chars:
            if len(result) >= _MICRO_MIN_CHARS:
                return micro_minify_tool(result, session_id, t0)
            return result, {"gate": "passthrough_small", "reason": "below_min_chars", "ms": 0.0}
        _rh = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
        # Store the original in CCR so retrieve(hash="#digest") resolves.
        try:
            _ccr_put(result)
        except Exception:
            pass
        seen_state = _seen(session_id or "default")
        failed_hashes = seen_state.setdefault("failed_raw", set())
        # Only a hash that previously FAILED (reverted_gate) and has no span in
        # the fold index may be skipped; a result that was compressed
        # successfully before must flow into fold_spans() again so it can be
        # folded down to a pointer instead of shipping in full.
        if _rh in failed_hashes and not any(h == _rh for h, _ in seen_state["spans"]):
            return result, {"gate": "passthrough_repeat", "ms": 0.0}
        if deadline is not None:
            deadline.check()
        text, h_saved = hygiene(result)
        text, v_saved = normalize_volatile(text)
        text, m_saved = minify_json(text)
        grouped, ss_saved = group_search_hits(text, owner=_rh, deadline=deadline)
        t_saved = 0
        toon_used = False
        f_saved = 0
        i_saved = 0
        dt_saved = 0
        if ss_saved > 0:
            folded = grouped
        else:
            folded, _, f_saved = fold_spans(text, session_id, owner=_rh, deadline=deadline)
        if ss_saved == 0 and f_saved == 0:
            folded, i_saved = _fold_intra_repeats(folded, deadline)
        if ss_saved == 0 and i_saved == 0:
            folded, dt_saved = _fold_intra_templates(folded, owner=_rh, deadline=deadline)
        
        ht_saved = 0
        ht_head = ht_tail = ht_omitted = 0
        sk_saved = 0
        if ss_saved == 0 and f_saved == 0 and i_saved == 0 and dt_saved == 0:
            # AST Code Skeletonizer check
            if len(folded) >= 2048 and (tool_name in ("view_file", "cat", "get_file") or "\ndef " in folded or "\nclass " in folded):
                try:
                    sk_cand = skeletonize_code(folded)
                    if len(sk_cand) < len(folded):
                        sk_saved = len(folded) - len(sk_cand)
                        folded = sk_cand
                except Exception:
                    pass

            # HEAD_TAIL check if not skeletonized
            if sk_saved == 0:
                stubbed, ht_saved = _fold_headtail(folded)
                if ht_saved > 0:
                    folded = stubbed
                    ht_lines = folded.split("\n")
                    ht_head = min(_HT_HEAD_LINES, len(ht_lines))
                    ht_tail = min(_HT_TAIL_LINES, max(0, len(ht_lines) - ht_head - 1))
                    ht_omitted = max(0, len(text.split("\n")) - ht_head - ht_tail)

        remember(text, session_id, owner=_rh)
        if toon_enabled:
            try:
                _cand = fold_json_to_toon(folded)
                if _cand != folded:
                    _cand = _TOON_LEGEND + "\n" + _cand
                    if len(_cand) < len(folded) and estimate_bpe_gain(folded, _cand) >= 4:
                        t_saved = len(folded) - len(_cand)
                        folded = _cand
                        toon_used = True
            except Exception:
                pass
        if len(folded) >= len(result):
            failed_hashes.add(_rh)
            return result, {
                "gate": "reverted_gate", "before_chars": len(result),
                "reason": "no_gain_fold",
                "ms": (time.perf_counter() - t0) * 1000,
            }
        digest = _rh
        is_lossless_only = (
            v_saved == 0 and ss_saved == 0 and f_saved == 0 and i_saved == 0
            and dt_saved == 0 and ht_saved == 0 and sk_saved == 0 and not toon_used
        )
        if ht_saved > 0:
            head = headtail_head(ht_head, ht_tail, ht_omitted, digest)
        else:
            head = adaptive_tool_head(
                len(result) - len(folded), digest, len(result),
                is_lossless_only=is_lossless_only,
            )
        new_result = f"{head}\n{folded}" if head else folded
        tok_before = estimate_tokens(result)
        tok_after = estimate_tokens(new_result)
        if len(new_result) >= len(result) or tok_after >= tok_before:
            # Cascade Gate Rescue: a header that costs more than it saves is
            # reverted wholesale today. Try the nano header first.
            if not is_lossless_only and ht_saved == 0:
                nano_head = adaptive_tool_head(
                    len(result) - len(folded), digest, len(result), force_tier=1)
                nano_cand = f"{nano_head}\n{folded}" if nano_head else folded
                nano_tok = estimate_tokens(nano_cand)
                if len(nano_cand) < len(result) and nano_tok < tok_before:
                    new_result, tok_after = nano_cand, nano_tok
            if len(new_result) >= len(result) or tok_after >= tok_before:
                failed_hashes.add(_rh)
                return result, {
                    "gate": "reverted_gate", "before_chars": len(result),
                    "reason": "header_cost",
                    "ms": (time.perf_counter() - t0) * 1000,
                }
        return new_result, {
            "gate": "ok", "digest": digest,
            "lossless_tier0": bool(is_lossless_only and not head),
            "before_chars": len(result), "after_chars": len(new_result),
            "saved_chars": len(result) - len(new_result),
            "est_tokens_before": tok_before,
            "est_tokens_after": tok_after,
            "hygiene_saved": h_saved, "volatile_saved": v_saved,
            "minify_saved": m_saved, "toon_saved": t_saved,
            "toon_used": toon_used,
            "fold_saved": f_saved, "intra_saved": i_saved,
            "intra_template_saved": dt_saved,
            "search_saved": ss_saved,
            "headtail_saved": ht_saved,
            "skeleton_saved": sk_saved,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    except TimeoutError:
        return result, {"gate": "passthrough_timeout", "reason": "deadline",
                        "ms": (time.perf_counter() - t0) * 1000}
    except Exception as _e:
        # Surface the cause: a swallowed exception here silently disables ALL
        # tool-result compression, which is invisible in telemetry without this.
        return result, {"gate": "passthrough_exception",
                        "error": f"{type(_e).__name__}: {_e}"[:300],
                        "ms": (time.perf_counter() - t0) * 1000}
