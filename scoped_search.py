"""trimtok scoped search grouping (Trimtok 2.0 Phase 2).

Multi-file search/grep results (``path:line:text`` hits) collapse per file:
a ``=== path (N hits) [defs: ...] ===`` header carries the file, hit count
and up to two def/class symbols, and only the first/last hit plus fault
lines stay verbatim — interior runs fold into standard ``[@#refxN]``
pointers that resolve through trimtok_retrieve.

Single-file docs are deliberately left alone: the template fold already
collapses those to a first line + one pointer (the locked 5-turn SIM
baseline routes through this gate), so grouping there could only add chars.

Polarity rule: fault islands (error/exception/HTTP-5xx lines) are never
folded — a poisoned hit must stay visible among clean ones.

Never raises. Net-save guard: returns the original text when grouping would
not make it smaller.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from .marker import is_important, seen_lines
except ImportError:  # direct unit-test import
    from marker import is_important, seen_lines

# path:line:text hit shape. Charset excludes quotes/braces so minified JSON
# strings never parse as hits; the lazy path still covers "C:\a\b.py:12:x".
_HIT_RE = re.compile(r"^([\w./\\~$@+:-]+?):(\d+):(.*)$")
_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|func(?:tion)?|fn)\s+([A-Za-z_$][\w$]*)"
)

_MIN_LINES = 12  # docs shorter than this never bother
_MIN_HITS = 10  # total hits needed before grouping may fire
_MIN_PATHS = 2  # multi-file only (see module docstring)
_HIT_FRACTION = 0.6  # share of non-empty lines that must parse as hits
_KEEP_ALL = 3  # segments at/below this size stay verbatim (no header)
_MIN_RUN = 2  # fold only runs of >= this many foldable hits
_MAX_DEFS = 2  # names listed before the "…+K" tail


def looks_search_shaped(text: str) -> bool:
    """True when the doc is a multi-file ``path:line:text`` hit list."""
    try:
        lines = [ln for ln in (text or "").split("\n") if ln.strip()]
        if len(lines) < _MIN_LINES:
            return False
        hits = 0
        paths: set[str] = set()
        for ln in lines:
            m = _HIT_RE.match(ln)
            if m:
                hits += 1
                paths.add(m.group(1))
        if hits < _MIN_HITS or len(paths) < _MIN_PATHS:
            return False
        return hits / len(lines) >= _HIT_FRACTION
    except Exception:
        return False


def _render_segment(
    path: str,
    hits: list[tuple[int, str, str]],
    owner: str | None,
    deadline: Any | None,
) -> list[str]:
    """Render one consecutive same-path run; raw lines when it won't fold."""
    raw = [h[2] for h in hits]
    n = len(hits)
    if n <= _KEEP_ALL:
        return raw
    keep = {0, n - 1}
    for i, (_, _, line) in enumerate(hits):
        # NOTE: is_exempt is deliberately NOT consulted here — its ":NN"
        # digit-dense rule would exempt every hit with a 2-5 digit line
        # number. In a hit list the line number is position metadata, so the
        # fault rail (is_important) is the only hard keep.
        if is_important(line):
            keep.add(i)
    defs: list[str] = []
    seen: set[str] = set()
    total_defs = 0
    for lineno, text, _ in hits:
        m = _DEF_RE.match(text)
        if m:
            total_defs += 1
            name = m.group(1)
            if name not in seen and len(defs) < _MAX_DEFS:
                seen.add(name)
                defs.append(f"{name}@L{lineno}")
    header = f"=== {path} ({n} hits)"
    if defs:
        extra = total_defs - len(defs)
        header += " [defs: " + " ".join(defs) + (f" …+{extra}" if extra > 0 else "") + "]"
    header += " ==="

    out: list[str] = []
    run: list[int] = []
    folded_any = False

    def _flush() -> None:
        nonlocal folded_any
        if not run:
            return
        if len(run) >= _MIN_RUN:
            out.append(seen_lines(len(run), owner or ""))
            folded_any = True
        else:
            out.extend(raw[k] for k in run)
        run.clear()

    for i in range(n):
        if deadline is not None and (i & 255) == 0:
            deadline.check()
        if i in keep:
            _flush()
            out.append(raw[i])
        else:
            run.append(i)
    _flush()
    if not folded_any:
        return raw
    return [header] + out


def group_search_hits(
    text: str,
    owner: str | None = None,
    deadline: Any | None = None,
) -> tuple[str, int]:
    """Collapse multi-file search hits into file-grouped blocks.

    Returns (text, saved_chars) — the original text when not applicable or
    when the grouped form would not be smaller.
    """
    try:
        if not looks_search_shaped(text):
            return text, 0
        lines = text.split("\n")
        out: list[str] = []
        seg_path: str | None = None
        seg_hits: list[tuple[int, str, str]] = []

        def _flush_seg() -> None:
            nonlocal seg_path, seg_hits
            if seg_path is not None:
                out.extend(_render_segment(seg_path, seg_hits, owner, deadline))
            seg_path = None
            seg_hits = []

        for ln in lines:
            m = _HIT_RE.match(ln)
            if m:
                path = m.group(1)
                if seg_path is not None and path != seg_path:
                    _flush_seg()
                seg_path = path
                seg_hits.append((int(m.group(2)), m.group(3), ln))
            else:
                _flush_seg()
                out.append(ln)
        _flush_seg()
        new_text = "\n".join(out)
        saved = len(text) - len(new_text)
        if saved <= 0:
            return text, 0
        return new_text, saved
    except Exception:
        return text, 0
