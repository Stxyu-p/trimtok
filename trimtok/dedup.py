"""trimtok Phase 2: tool-result compression (cross-turn span fold + hygiene + AST code skeletonization).

Stdlib only, pure functions + tiny module-level session corpus.
Never raises.
"""

from __future__ import annotations

import hashlib
import json as _json
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
        estimate_bpe_gain,
        estimate_tokens,
        fast_json_loads,
        headtail_head,
        is_exempt,
        is_important,
        masked,
        seen_line,
        seen_lines,
        shingle_head,
        template_head,
        tool_head,
    )
    from .pruners import group_search_hits, skeletonize_code
    from .toon_codec import fold_json_to_toon
except ImportError:
    from signals import (
        KEEP_PREFIX as _KEEP_PREFIX,
        LEGEND_TOON as _TOON_LEGEND,
        SENTINEL_RE,
        SLOT_RE as _SLOT_RE,
        estimate_bpe_gain,
        estimate_tokens,
        fast_json_loads,
        headtail_head,
        is_exempt,
        is_important,
        masked,
        seen_line,
        seen_lines,
        shingle_head,
        template_head,
        tool_head,
    )
    from pruners import group_search_hits, skeletonize_code
    from toon_codec import fold_json_to_toon

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


def compress_tool_result(
    tool_name: str,
    result: str,
    *,
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
            return result, {"gate": "passthrough_sentinel", "ms": 0.0}
        if len(result) < min_chars:
            return result, {"gate": "passthrough_small", "ms": 0.0}
        if tool_name == "read_file":
            try:
                _rt, _ = hygiene(result)
                _rt, _ = normalize_volatile(_rt)
                _rt, _ = minify_json(_rt)
                _rd = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
                remember(_rt, session_id, owner=_rd)
            except Exception:
                pass
            return result, {"gate": "passthrough_reference", "ms": 0.0}
        _rh = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
        raw_hashes = _seen(session_id or "default")["raw"]
        if _rh in raw_hashes:
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
            raw_hashes.add(_rh)
            return result, {
                "gate": "reverted_gate", "before_chars": len(result),
                "ms": (time.perf_counter() - t0) * 1000,
            }
        digest = _rh
        raw_hashes.add(_rh)
        if ht_saved > 0:
            head = headtail_head(ht_head, ht_tail, ht_omitted, digest)
        else:
            head = tool_head(len(result) - len(folded), digest)
        new_result = head + "\n" + folded
        if len(new_result) >= len(result):
            return result, {"gate": "reverted_gate", "before_chars": len(result),
                            "ms": (time.perf_counter() - t0) * 1000}
        return new_result, {
            "gate": "ok", "digest": digest,
            "before_chars": len(result), "after_chars": len(new_result),
            "saved_chars": len(result) - len(new_result),
            "est_tokens_before": estimate_tokens(result),
            "est_tokens_after": estimate_tokens(new_result),
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
        return result, {"gate": "passthrough_timeout",
                        "ms": (time.perf_counter() - t0) * 1000}
    except Exception:
        return result, {"gate": "passthrough_exception",
                        "ms": (time.perf_counter() - t0) * 1000}
