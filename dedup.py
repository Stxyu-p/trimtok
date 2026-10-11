"""trimtok Phase 2: tool-result compression (cross-turn span fold + hygiene).

Stdlib only, pure functions + tiny module-level session corpus.
Never raises.
"""

from __future__ import annotations

import hashlib
import re
import time

SENTINEL_RE = re.compile(r"\[tt:[a-z]+\b")
_KEEP_PREFIX = "TRIMTOK_KEEP:"
_MIN_SPAN_LINES = 5
_MIN_SPAN_CHARS = 200
_MIN_LINE_CHARS = 40  # single repeated lines at/above this fold individually
_MAX_SPANS = 200  # per-session corpus cap (ponytail: global cap, LRU by insert)

_WS_TRAIL_RE = re.compile(r"[ \t]+(?=\n|$)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
)
_SESS_RE = re.compile(r"\b20\d{6}_\d{6}_[0-9a-f]{4,}\b")
_TS_LONG_RE = re.compile(
    r"\b\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b"
)

# session_id -> {"spans": [(hash, text)], "lines": {line: span_hash},
#                  "raw": set(raw_hashes for repeat detection),
#                  "turns": {turn_id: {"hashes": [raw digests in order],
#                                      "masked": bool}}}
_SEEN: dict[str, dict] = {}


def _corpus(session_id: str) -> list[tuple[str, str]]:
    return _seen(session_id)["spans"]


def _repeat_seen(session_id: str) -> set[str]:
    return _seen(session_id)["raw"]


def _seen(session_id: str) -> dict:
    key = session_id or "default"
    entry = _SEEN.get(key)
    if entry is None:
        entry = {"spans": [], "lines": {}, "raw": set(), "turns": {}}
        _SEEN[key] = entry
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
    """Drop fold-index entries (spans + lines) older than keep_turns turns.

    This is the mask's real lever inside hook limits: hooks can't rewrite
    written history, but stale content stops matching new queries. CCR
    originals are untouched — trimtok_retrieve keeps resolving. Never raises.
    Returns expired turn count.
    """
    try:
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
        seen["spans"] = [(h, t) for h, t in seen["spans"] if h not in doomed]
        for ln, owner in list(seen["lines"].items()):
            if owner in doomed:
                del seen["lines"][ln]
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
    head = f"[tt:masked chars={saved_chars} retrieve:{digest}]"
    return head + (f"\nStarts with: {first}" if first else "")


def hygiene(text: str) -> tuple[str, int]:
    """Trailing-ws strip + blank-run collapse. Returns (text, saved)."""
    new = _WS_TRAIL_RE.sub("", text)
    new = _BLANK_RUN_RE.sub("\n\n", new)
    return new, len(text) - len(new)


def normalize_volatile(text: str) -> tuple[str, int]:
    """UUID/session-id/timestamp -> stable placeholders (cache-friendly)."""
    new = _UUID_RE.sub("<UUID>", text)
    new = _SESS_RE.sub("<SESS>", new)
    new = _TS_LONG_RE.sub("<TS>", new)
    return new, len(text) - len(new)


def _index_lines(span_lines: list[str], corpus: list[tuple[str, str]]) -> None:
    for h, _ in corpus:
        pass  # hashes compared directly; kept simple, corpus is small


def fold_spans(
    text: str,
    session_id: str = "",
    *,
    min_lines: int = _MIN_SPAN_LINES,
    min_chars: int = _MIN_SPAN_CHARS,
    owner: str | None = None,
) -> tuple[str, list[tuple[str, str]], int]:
    """Replace verbatim runs seen in earlier tool results with pointers.

    Returns (new_text, new_spans_to_store, folded_chars).
    Pointer format: [tt:seen lines=M retrieve:H] — H resolves via CCR.
    owner: digest the CALLER stores the raw original under (CCR key). All
    pointers embed it, so every pointer resolves. Defaults to first-seen
    (unit-test path only — production always passes the current digest).
    """
    corpus = _corpus(session_id)
    seen = _seen(session_id)
    # query folds at two levels: (1) raw lines against the index, (2) inner
    # lines of JSON string fields (a minified envelope is one giant line, so
    # the raw split alone has nothing to match indexed content). Level 2 is
    # contained: folded inner lines stay INSIDE the same envelope string.
    lines = text.split("\n")
    if not corpus:
        return text, [], 0
    # map first-line -> span indices for cheap lookup
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
        line = lines[i]
        # single repeated line: fold individually, keep first occurrence verbatim.
        # Pointer embeds the CURRENT block digest (owner), never first-seen:
        # resolvability beats provenance — first-seen hashes point at blocks
        # that may have reverted (never stored in CCR).
        if (
            line_owner.get(line) is not None
            and len(line) >= _MIN_LINE_CHARS
            and not line.startswith("[tt:")
            and not line.startswith(_KEEP_PREFIX)
        ):
            ptr = f"[tt:seen line retrieve:{owner or line_owner[line]}]"
            out.append(ptr)
            folded_chars += len(line) - len(ptr)
            i += 1
            continue
        best: tuple[int, int, str] | None = None  # (length, span_idx, hash)
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
            ptr = f"[tt:seen lines={m} retrieve:{owner or _h}]"
            out.append(ptr)
            folded_chars += len(seg) - len(ptr)
            i += m
        else:
            # level 2: fold inner lines of a JSON string field, contained
            # inside the SAME envelope string (no duplication, valid JSON kept)
            folded_line, extra = _fold_inner(lines[i], line_owner, owner)
            out.append(folded_line)
            folded_chars += extra
            i += 1
    return "\n".join(out), new_spans, folded_chars


def _fold_inner(
    line: str, line_owner: dict[str, str], owner: str | None = None
) -> tuple[str, int]:
    """Fold indexed inner lines inside one JSON envelope line. Contained."""
    if len(line) < 500 or line.lstrip()[:1] not in "{[":
        return line, 0
    try:
        import json as _json

        data = _json.loads(line)
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
                    ptr = f"[tt:seen line retrieve:{owner or line_owner[ln]}]"
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


def remember(text: str, session_id: str = "", owner: str | None = None) -> str:
    """Index result + its big inner content for future folding. Returns hash.

    owner: raw-content digest of the owning block. Line/span pointers embed
    this hash, and the hook stores the raw original in CCR under it — so
    every pointer resolves via trimtok_retrieve. Defaults to the span hash
    (unit-test path; resolution via hook CCR does not apply there).
    """
    seen = _seen(session_id)
    corpus = seen["spans"]
    h = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    key = owner or h
    if any(eh == key for eh, _ in corpus):
        return key
    if len(corpus) >= _MAX_SPANS:
        old_h, _ = corpus.pop(0)  # ponytail: LRU by insert order, good enough
        for ln, ln_owner in list(seen["lines"].items()):
            if ln_owner == old_h:
                del seen["lines"][ln]
    corpus.append((key, text))
    # index every long verbatim line (incl. lines inside JSON string fields)
    # so later results fold against earlier ones; first occurrence stays verbatim
    for ln in text.split("\n"):
        if len(ln) >= _MIN_LINE_CHARS and not ln.startswith("[tt:"):
            seen["lines"].setdefault(ln, key)
    # JSON string fields: index their inner lines too (envelope differs,
    # content repeats — e.g. SKILL.md loaded twice, once minified once not).
    # Any string field (no 500-char floor): a minified envelope has ONE giant
    # line, so its inner content lines would otherwise never get indexed.
    if text.lstrip()[:1] in "{[":
        try:
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
                        if len(ln) >= _MIN_LINE_CHARS and not ln.startswith("[tt:"):
                            seen["lines"].setdefault(ln, key)

            _walk(_json.loads(text))
        except Exception:
            pass
    return key


def clear_session(session_id: str = "") -> None:
    _SEEN.pop(session_id or "default", None)


def mask_old_turns(
    session_id: str = "",
    current_turn: str = "",
    *,
    keep_turns: int = _MASK_KEEP_TURNS,
    store: dict[str, str] | None = None,
) -> dict[str, str]:
    """Replace stored originals older than keep_turns with placeholders.

    store: raw digest -> original text (caller's CCR mirror). Returns
    {digest: placeholder}. Masked originals STAY in the CCR — pointers and
    trimtok_retrieve keep resolving. Idempotent per turn (masked flag).
    Never raises.
    """
    out: dict[str, str] = {}
    try:
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


_TOON_LEGEND = (
    "[tt:legend] Table below is TOON: header [N]{f1,f2} lists fields, "
    "each following line is one object, values in header order."
)
_TOON_MIN_ROWS = 3


def toon_encode(rows: list[dict], *, max_rows: int = 0) -> tuple[str, bool]:
    """Minimal TOON for uniform dict arrays (stdlib, no dep).

    Returns (text, complete). complete=False when rows were capped (head+tail
    sample with omission marker). Never raises.
    """
    try:
        if not rows or not all(isinstance(r, dict) for r in rows):
            return "", False
        fields: list[str] = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
        # uniform check: every row has exactly the field set
        if any(set(r.keys()) != set(fields) for r in rows):
            return "", False
        # flat scalars only (no nested dict/list — keep it dumb and safe)
        for r in rows:
            for v in r.values():
                if not isinstance(v, (str, int, float, bool)) and v is not None:
                    return "", False
        total = len(rows)
        capped = False
        if max_rows and total > max_rows:
            keep_h = max_rows // 2
            keep_t = max_rows - keep_h
            sample = rows[:keep_h] + rows[total - keep_t:]
            capped = True
        else:
            sample = rows

        def _cell(v: object) -> str:
            if v is None:
                return ""
            if isinstance(v, bool):
                return "true" if v else "false"
            s = str(v)
            if any(c in s for c in (",", '"', "\n", "[")):
                return '"' + s.replace('"', '""') + '"'
            return s

        lines = [f"[{total}]{{{','.join(fields)}}}:"] if not capped else [
            f"[{total}]{{{','.join(fields)}}} (showing {len(sample)}):"]
        for r in sample:
            lines.append("  " + ",".join(_cell(r.get(f)) for f in fields))
        if capped:
            lines.append(f"[... {total - len(sample)} rows omitted ...]")
        return "\n".join(lines), not capped
    except Exception:
        return "", False


def minify_json(text: str) -> tuple[str, int]:
    """Lossless JSON minify (whole-doc only, never partial). Returns (text, saved)."""
    try:
        import json as _json

        stripped = text.strip()
        if not stripped or stripped[0] not in "{[":
            return text, 0
        data = _json.loads(stripped)
        mini = _json.dumps(data, separators=(",", ":"), ensure_ascii=False)
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
    toon_enabled: bool = False,
) -> tuple[str, dict]:
    """Full Phase 2 pipeline on one tool result. Never raises."""
    t0 = time.perf_counter()
    try:
        if not isinstance(result, str):
            return result, {"gate": "passthrough_non_str", "ms": 0.0}
        # identity checks BEFORE the size gate: sentinel/reference/repeat are
        # structural, not size-dependent — a folded pointer is small by design
        # and must still be recognized on re-entry (idempotency, rule 5).
        if SENTINEL_RE.search(result[:2000]):
            return result, {"gate": "passthrough_sentinel", "ms": 0.0}
        if len(result) < min_chars:
            return result, {"gate": "passthrough_small", "ms": 0.0}
        if tool_name == "read_file":
            # reference data stays verbatim — but still index it for others.
            # (Normalized, like the main path: fold queries normalized lines.)
            try:
                _rt, _ = hygiene(result)
                _rt, _ = normalize_volatile(_rt)
                _rt, _ = minify_json(_rt)
                _rd = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
                remember(_rt, session_id, owner=_rd)
            except Exception:
                pass
            return result, {"gate": "passthrough_reference", "ms": 0.0}
        # repeat→passthrough (SPEC v0.2 rule 2): agent re-ran the tool to get
        # dropped detail back — a deterministic re-fold would hide it again
        # and fool the agent into thinking the tool is broken.
        # (Checked on raw input: an exact re-run means the agent wants it full.)
        _rh = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()[:12]
        if _rh in _repeat_seen(session_id or "default"):
            return result, {"gate": "passthrough_repeat", "ms": 0.0}
        text, h_saved = hygiene(result)
        text, v_saved = normalize_volatile(text)
        # lossless JSON minify first (default-on): whole-doc only, value-identical
        text, m_saved = minify_json(text)
        t_saved = 0
        toon_used = False
        # fold FIRST against prior corpus, remember AFTER: indexing before
        # folding lets a doc fold against itself (self-fold — every line
        # "seen"). First occurrence must stay verbatim (rule: keep-earliest).
        # Every pointer embeds _rh (== _raw_digest below): the hook stores the
        # raw original under it, so all pointers resolve via trimtok_retrieve.
        folded, _, f_saved = fold_spans(text, session_id, owner=_rh)
        # remember the NORMALIZED text (fold queries normalized lines) but
        # under the RAW digest: pointers embed it and CCR stores the raw
        # original under it, so every pointer resolves via trimtok_retrieve.
        # (_rh IS the raw digest — computed above from result.)
        remember(text, session_id, owner=_rh)
        # TOON AFTER fold (default-off, behavior-gated): a whole-doc encoding —
        # folding its emitted table lines against the corpus would eat the
        # legend + rows, so it recodes the folded text as a final step.
        if toon_enabled:
            try:
                import json as _json

                _data = _json.loads(folded.strip())
                if isinstance(_data, list) and len(_data) >= _TOON_MIN_ROWS:
                    _toon, _complete = toon_encode(_data)
                    if _toon:
                        _cand = _TOON_LEGEND + "\n" + _toon
                        if len(_cand) < len(folded):
                            t_saved = len(folded) - len(_cand)
                            folded = _cand
                            toon_used = True
            except Exception:
                pass
        # token-gate
        if len(folded) >= len(result):
            _repeat_seen(session_id or "default").add(_rh)
            return result, {
                "gate": "reverted_gate", "before_chars": len(result),
                "ms": (time.perf_counter() - t0) * 1000,
            }
        digest = _rh
        _repeat_seen(session_id or "default").add(_rh)
        head = f"[tt:tool kind=result saved={len(result) - len(folded)} retrieve:{digest}]"
        new_result = head + "\n" + folded
        if len(new_result) >= len(result):
            return result, {"gate": "reverted_gate", "before_chars": len(result),
                            "ms": (time.perf_counter() - t0) * 1000}
        return new_result, {
            "gate": "ok", "digest": digest,
            "before_chars": len(result), "after_chars": len(new_result),
            "saved_chars": len(result) - len(new_result),
            "hygiene_saved": h_saved, "volatile_saved": v_saved,
            "minify_saved": m_saved, "toon_saved": t_saved,
            "toon_used": toon_used,
            "fold_saved": f_saved,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    except Exception:
        return result, {"gate": "passthrough_exception",
                        "ms": (time.perf_counter() - t0) * 1000}
