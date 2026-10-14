"""trimtok marker: one module for the [tt:...] shape (arch C1).

Every marker the pipeline emits and every gate that skips compressed output
go through here. Format change happens once. Never raises.
"""

from __future__ import annotations

import re

SENTINEL_RE = re.compile(r"\[tt:(?:legend\]|(?:terminal|tool|seen|masked|template|shingle|headtail)\b[^\]]*retrieve:[0-9a-f]+)")

# Single source of truth (zero-spaghetti law 1): KEEP prefix + template-slot
# regex live here because core + dedup both need them. The slot pattern is
# the stronger of the two historical copies: volatile placeholders, UUIDs,
# short times, and digits inside identifiers (func_0) all become {}.
KEEP_PREFIX = "TRIMTOK_KEEP:"

# Error preservation patterns (Priority 1: Zero Loss of Intelligence)
_ERROR_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(error(?:s)?|exception|traceback|fail(?:ed|ure)?|panic|fatal|critical|"
    r"warn(?:ing)?|denied|refused|timeout|seg(?:fault|mentation\s+fault)|"
    r"assert(?:ion)?(?:error|ion| failed)?|oom|epipe|"
    r"exit\s+code\s+[1-9]|out\s+of\s+memory|"
    r"ข้อผิดพลาด|ผิดพลาด|ล้มเหลว|ขัดข้อง)"
    r"(?![A-Za-z0-9_])"
    r"|^\s*(File |  at |Caused by:)"
)

_ERROR_HINTS = (
    "error", "exception", "traceback", "fail", "panic", "fatal", "critical",
    "warn", "denied", "refused", "timeout", "segfault", "segment", "fault",
    "assert", "oom", "epipe", "exit code", "out of memory", "memory",
    "ผิดพลาด", "ล้มเหลว", "ขัดข้อง", "ข้อผิดพลาด",
    "file ", "  at ", "caused by:",
)


def looks_suspicious(low: str) -> bool:
    for h in _ERROR_HINTS:
        if h in low:
            return True
    return False


def is_important(line: str) -> bool:
    """True when a line contains an error, exception, or failure indicator."""
    if not line:
        return False
    if line.startswith(KEEP_PREFIX):
        return True
    if not looks_suspicious(line.lower()):
        return False
    return bool(_ERROR_RE.search(line))

SLOT_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|<TAG>|<UUID>|<SESS>|<TS>|\b\d{1,2}:\d{2}:\d{2}\b|(?<!\d)\d+",
    re.I,
)

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


# Byte-class token estimator (Step 3, stats-only): pure-stdlib, single-pass
# C-level regex. Never used for gate decisions (gates compare len() only).
# Accuracy target +-5-8% on Thai/CJK + symbol-heavy code vs chars//4.
_RE_THAI_CJK = re.compile(r"[\u0e00-\u0e7f\u4e00-\u9fff\u3040-\u30ff]")
_RE_PUNCT = re.compile(r"[{}()\[\].,;:=+\-*/<>!&|^~%@#$\\]")


def estimate_tokens(text: str) -> int:
    """Rough BPE-ish token estimate without tiktoken. Never raises."""
    try:
        if not text or not isinstance(text, str):
            return 0
        n_chars = len(text)
        if n_chars == 0:
            return 0
        # ponytail: two findall scans, C-speed; <0.05ms typical, <1ms at 1MB.
        # Upgrade path: single-pass translation table if profiling says so.
        n_punct = len(_RE_PUNCT.findall(text))
        if text.isascii():
            return max(1, (n_chars - n_punct) // 4 + (n_punct * 2) // 3)
        n_thai_cjk = len(_RE_THAI_CJK.findall(text))
        n_rest = n_chars - n_thai_cjk - n_punct
        return max(1, (n_rest // 4) + ((n_punct * 2) // 3) + int(n_thai_cjk * 0.9))
    except Exception:
        return 0


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
