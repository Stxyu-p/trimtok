"""trimtok Phase 2: tool-result compression (cross-turn span fold + hygiene).

Stdlib only, pure functions + tiny module-level session corpus.
Never raises.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

try:
    from .marker import KEEP_PREFIX as _KEEP_PREFIX
    from .marker import SENTINEL_RE, SLOT_RE as _SLOT_RE, estimate_tokens, headtail_head, is_exempt, is_important, masked, seen_line, seen_lines, shingle_head, template_head, tool_head
    from .marker import LEGEND_TOON as _TOON_LEGEND
except ImportError:  # direct unit-test import
    from marker import KEEP_PREFIX as _KEEP_PREFIX
    from marker import SENTINEL_RE, SLOT_RE as _SLOT_RE, estimate_tokens, headtail_head, is_exempt, is_important, masked, seen_line, seen_lines, shingle_head, template_head, tool_head
    from marker import LEGEND_TOON as _TOON_LEGEND
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
# W7: short HH:MM:SS without a date ("10:00:05 INFO ..."). Seconds required
# and digit/colon boundaries on both sides, so "file.py:12:30" style
# line:col references never match (preceded by ":"). HH:MM alone is too
# risky (versions, ratios) and stays verbatim.
_TS_SHORT_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])")

# session_id -> {"spans": [(hash, text)], "span_chars": int,
#                  "lines": {line: span_hash},
#                  "raw": set(raw_hashes for repeat detection),
#                  "turns": {turn_id: {"hashes": [raw digests in order],
#                                      "masked": bool}}}
_SEEN: dict[str, dict] = {}


def _seen(session_id: str) -> dict:
    key = session_id or "default"
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
    """Replace verbatim runs seen in earlier tool results with pointers.

    Returns (new_text, new_spans_to_store, folded_chars).
    Pointer format: [tt:seen lines=M retrieve:H] — H resolves via CCR.
    owner: digest the CALLER stores the raw original under (CCR key). All
    pointers embed it, so every pointer resolves. Defaults to first-seen
    (unit-test path only — production always passes the current digest).
    """
    seen = _seen(session_id)
    corpus = seen["spans"]
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
        if deadline is not None and (i & 255) == 0:
            deadline.check()
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
            and not is_exempt(line)
            and not is_important(line)
        ):
            ptr = seen_line(owner or line_owner[line])
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
            ptr = seen_lines(m, owner or _h)
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
    # level 3 (shingle near-dedup): survivors that match no verbatim run get
    # a second chance against prior corpus lines at Jaccard>=0.8. Runs after
    # the verbatim loop so exact hits keep the cheaper [tt:seen] pointers.
    # Gated on folded_chars == 0: the pass costs ~2ms on a 60-line query
    # (measured 2026-10-12: cross-turn median 0.54ms -> 2.83ms), so it only
    # runs when verbatim found nothing — same second-chance pattern as
    # intra/headtail. Partial verbatim hits keep their pointers as-is.
    if folded_chars == 0 and out:
        corpus_long = [ln for ln in line_owner if len(ln.split()) >= _SHINGLE_MIN_WORDS]
        if corpus_long:
            out, sh_saved = _fold_shingles(out, corpus_long, owner, deadline)
            folded_chars += sh_saved
    return "\n".join(out), new_spans, folded_chars


def _fold_intra_repeats(text: str, deadline: Any | None = None) -> tuple[str, int]:
    """Fold adjacent duplicate line runs inside one first-seen document.

    W4: terminal folds repeats within its own output; the tool path only
    folded against the prior corpus, so a repetitive first-seen doc reverted
    at the token-gate. Keep the first line of each run verbatim, fold the
    rest as ``[repeated Nx]`` (same marker shape as the terminal path).
    Only runs of 3+ fold; short/important-shaped lines never fold.
    """
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


# Drain-style intra template fold (core._fold_templates shape for one doc):
# slot pattern (_SLOT_RE) imported from marker: same single source of truth.
# Same contract as terminal: first occurrence verbatim (keep-earliest),
# groups of <3 pass through, exempt/KEEP/marker lines never group.
# NOTE (Step 4 reverted 2026-10-14): signature pinning (def/class rail) was
# tried here and REJECTED — it kept all 400 SIM search sig lines verbatim,
# regressing SIM 61.2% -> 30.0% (-31.2pp). One-line search hits fold via
# templates by design; naked sigs without slots never group anyway.


def _intra_template_key(line: str) -> str:
    norm = line.strip()
    if len(norm) < 20:
        return ""
    key = _SLOT_RE.sub("{}", norm)
    if len(key) < 20:
        return ""
    # a key with no slot is an exact-duplicate group — intra-repeats owns it
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


_HT_MIN_CHARS = 100_000  # only large docs earn a stub; small unique stays full
_HT_HEAD_LINES = 30
_HT_TAIL_LINES = 30


def _fold_headtail(text: str) -> tuple[str, int]:
    """HEAD_TAIL stub for large non-repetitive docs (research lossless-compact).

    When nothing folded (no cross-turn hit, no intra repeats), a large unique
    doc reverts to 0%. Instead keep head+tail verbatim and stub the middle —
    same shape as the terminal window, recoverable via CCR. Small docs still
    revert (keep-earliest); marker lines never stub.
    """
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
    """3-word shingle fingerprint (claw-compactor semantic_dedup shape)."""
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
    """Fold near-duplicate lines (Jaccard>=0.8) against prior corpus lines.

    Verbatim matching misses lines that share a template but differ in slots
    (counters, timestamps, ids). Keep the first corpus occurrence verbatim;
    later near-dups collapse into one [tt:shingle] pointer per line.
    Only lines LONGER than pointer+sample fold (short lines pass through);
    markers and KEEP lines never fold.
    """
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
                continue  # verbatim hits belong to the verbatim path
            if _jaccard(fp, cfp) >= _SHINGLE_MIN_JACCARD:
                # bare pointer: the template is verbatim in the corpus
                # (keep-first), so no sample is needed to stay readable
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
    """Index lines inside JSON string fields, tolerating a trailing tail.

    W-D10: ``remember()`` skipped inner indexing whenever ``json.loads(text)``
    failed — e.g. a JSON envelope followed by a ``\\n`` + tail. The envelope's
    inner lines then never folded against later queries. Try the whole doc,
    then the longest leading-JSON prefix (raw_decode), then each long line.
    """
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
            _walk(_json.loads(text))
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
                _walk(_json.loads(s))
            except Exception:
                continue


def remember(text: str, session_id: str = "", owner: str | None = None) -> str:
    """Index content for future folding within bounded per-session budgets.

    Returns hash. Results larger than the corpus budget are not retained; the
    raw payload remains available through the caller's normal storage path.

    owner: raw-content digest of the owning block. Line/span pointers embed
    this hash, and the hook stores the raw original in CCR under it — so
    every pointer resolves via trimtok_retrieve. Defaults to the span hash
    (unit-test path; resolution via hook CCR does not apply there).
    """
    if not isinstance(text, str):
        return owner or ""
    seen = _seen(session_id)
    corpus = seen["spans"]
    key = owner or hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    if any(eh == key for eh, _ in corpus):
        return key
    if len(text) > _MAX_SPAN_CHARS:
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
    # index every long verbatim line (incl. lines inside JSON string fields)
    # so later results fold against earlier ones; first occurrence stays verbatim
    for ln in text.split("\n"):
        _index_long_line(seen, ln, key)
    # JSON string fields: index their inner lines too (envelope differs,
    # content repeats — e.g. SKILL.md loaded twice, once minified once not).
    # Tolerates a trailing non-JSON tail via _index_json_inners (W-D10).
    _index_json_inners(seen, text, key)
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


_TOON_MIN_ROWS = 3


def toon_encode(rows: list[dict], *, max_rows: int = 0) -> tuple[str, bool]:
    """Minimal TOON for uniform dict arrays (stdlib, no dep).

    Returns (text, complete). complete=False when rows were capped (head+tail
    sample with omission marker). Never raises.
    """
    try:
        if not rows or not all(isinstance(r, dict) for r in rows):
            return "", False
        # uniform check: every row has exactly the field set IN THE SAME ORDER
        # (squeez toon.rs strict shape: key order mismatch = reject)
        first_keys = list(rows[0].keys())
        if any(list(r.keys()) != first_keys for r in rows):
            return "", False
        fields = first_keys
        # flat scalars only (no nested dict/list — keep it dumb and safe)
        for r in rows:
            for v in r.values():
                if not isinstance(v, (str, int, float, bool)) and v is not None:
                    return "", False
        # Field names form the unquoted header; reject names that would make
        # its delimiters ambiguous rather than emitting a corrupt table.
        if any(any(c in k for c in ',{}[]"\r\n') for k in fields):
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
                return "null"
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, str):
                return '"' + v.replace('"', '""') + '"'
            s = str(v)
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
        try:
            data = _json.loads(stripped)
            tail = ""
        except Exception:
            # W-D10: JSON envelope + trailing non-JSON tail — minify the
            # leading JSON prefix via raw_decode, keep the tail verbatim.
            try:
                data, idx = _json.JSONDecoder().raw_decode(stripped)
            except Exception:
                return text, 0
            tail = stripped[idx:]
            if len(tail) > 4096 or not tail.strip():
                return text, 0
            # Never partial-minify when the tail itself looks like more
            # JSON: '{"a": 1} trailing garbage {"a": 1} ...' stays untouched
            # (existing minify-partial-no). Only a plain non-JSON tail
            # (log lines, padding) allows prefix minify.
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
    toon_enabled: bool = False,
    deadline: Any | None = None,
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
        raw_hashes = _seen(session_id or "default")["raw"]
        if _rh in raw_hashes:
            return result, {"gate": "passthrough_repeat", "ms": 0.0}
        if deadline is not None:
            deadline.check()
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
        folded, _, f_saved = fold_spans(text, session_id, owner=_rh, deadline=deadline)
        i_saved = 0
        if f_saved == 0:
            # W4: intra-document repeat fold on FIRST-SEEN docs. fold_spans
            # only folds against the prior corpus, so a repetitive doc with
            # nothing before it reverts (1MB repeat blob: 0%). Fold adjacent
            # normalized-duplicate line runs inside this doc itself — first
            # line of each run stays verbatim (keep-earliest holds
            # within-document; CCR stores the raw original).
            folded, i_saved = _fold_intra_repeats(folded, deadline)
        dt_saved = 0
        if i_saved == 0:
            # Drain templates within one doc (D7 volatile shape): scattered
            # same-template lines no adjacent-repeat pass can see. Same
            # second-chance position as intra-repeats — verbatim won already.
            folded, dt_saved = _fold_intra_templates(folded, owner=_rh, deadline=deadline)
        ht_saved = 0
        ht_head = ht_tail = ht_omitted = 0
        if f_saved == 0 and i_saved == 0 and dt_saved == 0:
            # HEAD_TAIL: large unique doc — nothing folded. Keep head+tail
            # verbatim, stub the middle (terminal-window shape, CCR recovers).
            stubbed, ht_saved = _fold_headtail(folded)
            if ht_saved > 0:
                folded = stubbed
                ht_lines = folded.split("\n")
                ht_head = min(_HT_HEAD_LINES, len(ht_lines))
                ht_tail = min(_HT_TAIL_LINES, max(0, len(ht_lines) - ht_head - 1))
                ht_omitted = max(0, len(text.split("\n")) - ht_head - ht_tail)
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
            "headtail_saved": ht_saved,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    except TimeoutError:
        return result, {"gate": "passthrough_timeout",
                        "ms": (time.perf_counter() - t0) * 1000}
    except Exception:
        return result, {"gate": "passthrough_exception",
                        "ms": (time.perf_counter() - t0) * 1000}
