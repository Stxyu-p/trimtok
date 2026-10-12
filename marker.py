"""trimtok marker: one module for the [tt:...] shape (arch C1).

Every marker the pipeline emits and every gate that skips compressed output
go through here. Format change happens once. Never raises.
"""

from __future__ import annotations

import re

SENTINEL_RE = re.compile(r"\[tt:(?:legend\]|(?:terminal|tool|seen|masked|template|shingle|headtail)\b[^\]]*retrieve:[0-9a-f]+)")

LEGEND_FOLD = "[tt:legend] Lines folded as [repeated Nx]. Full original in CCR, use trimtok_retrieve."
LEGEND_TOON = (
    "[tt:legend] Table below is TOON: header [N]{f1,f2} lists fields, "
    "each following line is one object, values in header order."
)


def terminal_head(kind: str, hidden: int, digest: str) -> str:
    return f"[tt:terminal kind={kind} hidden={hidden} retrieve:{digest}]"


def tool_head(saved: int, digest: str) -> str:
    return f"[tt:tool kind=result saved={saved} retrieve:{digest}]"


def seen_lines(count: int, digest: str) -> str:
    return f"[tt:seen lines={count} retrieve:{digest}]"


def seen_line(digest: str) -> str:
    return f"[tt:seen line retrieve:{digest}]"


def masked(chars: int, digest: str) -> str:
    return f"[tt:masked chars={chars} retrieve:{digest}]"


def template_head(count: int, digest: str) -> str:
    return f"[tt:template lines={count} retrieve:{digest}]"


def shingle_head(count: int, digest: str) -> str:
    return f"[tt:shingle lines={count} retrieve:{digest}]"


def headtail_head(kept_head: int, kept_tail: int, omitted: int, digest: str) -> str:
    return f"[tt:headtail head={kept_head} tail={kept_tail} omitted={omitted} retrieve:{digest}]"


_HASH_RE = re.compile(r"retrieve:([0-9a-f]{12,16})")


# W-guards: digit-dense identifiers. NOTE (learned the hard way):
#  - `:\d{2,5}` needs (?<!\d) or "20:00:01" timestamps exempt everything
#  - "line\s+\d+" matches prose "content line 30" — source-line refs are
#    already covered by error rails (important never folds)
_EXEMPT_RE = re.compile(
    r"(?i)port\s+\d{2,5}|(?<!\d):\d{2,5}\b|[0-9a-f]{7,40}|"
    r"\b\d{3,}\s+(errors|lines|files|rows|tests|passed|failed)\b"
)


def is_exempt(line: str) -> bool:
    """Never-fold lines (LLMLingua-2 force-reserve shape): digit-dense
    identifiers (ports, counts, hashes, line numbers) fold unsafely — the
    number IS the content. KEEP lines always exempt. Never raises."""
    try:
        if (line or "").startswith("TRIMTOK_KEEP:"):
            return True
        return bool(_EXEMPT_RE.search(line or ""))
    except Exception:
        return False


def find_hashes(text: str) -> list[str]:
    """All retrieve hashes in a marker-carrying output. Never raises."""
    try:
        return _HASH_RE.findall(text or "")
    except Exception:
        return []


def is_compressed(text: str) -> bool:
    """True when the head window carries a real marker (sentinel gate)."""
    try:
        return bool(SENTINEL_RE.search((text or "")[:2000]))
    except Exception:
        return False
