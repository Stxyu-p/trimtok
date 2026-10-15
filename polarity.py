"""trimtok polarity: fault-island classification (Trimtok 2.0 Phase 2).

One home for the negative-polarity rails both compressors consult: a line
that carries an error/exception/panic/HTTP-5xx/Thai-failure signal is a
fault island — folds must never swallow it, so `is_important` (marker.py)
and every fold stage route through `is_fault_line`.

core.py and marker.py each used to carry their own drifting copy of this
table; both now import from here. Coverage added in Phase 2: HTTP-status
faults (`status=500`, `HTTP/1.1 502`, `bad gateway`) — the plan's fidelity
canary hides a `status=500` among `status=200` lines and it must stay
visible 100% of the time.

Never raises.
"""

from __future__ import annotations

import re

# Negative-polarity patterns (Priority 1: Zero Loss of Intelligence).
# The prefilter below is a fast substring scan; this regex is the decider.
_ERROR_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(error(?:s)?|exception|traceback|fail(?:ed|ure)?|panic|fatal|critical|"
    r"warn(?:ing)?|denied|refused|timeout|seg(?:fault|mentation\s+fault)|"
    r"assert(?:ion)?(?:error|ion| failed)?|oom|epipe|"
    r"exit\s+code\s+[1-9]|out\s+of\s+memory|"
    r"status(?:_code)?[=:\s]+5\d{2}(?![0-9])|"
    r"http[/\d.\s]*5\d{2}(?![0-9])|bad\s+gateway|"
    r"ข้อผิดพลาด|ผิดพลาด|ล้มเหลว|ขัดข้อง)"
    r"(?![A-Za-z0-9_])"
    r"|^\s*(File |  at |Caused by:)"
)

# Fast pre-filter: single-pass substring scan (superset of the regex words).
_ERROR_HINTS = (
    "error", "exception", "traceback", "fail", "panic", "fatal", "critical",
    "warn", "denied", "refused", "timeout", "segfault", "segment", "fault",
    "assert", "oom", "epipe", "exit code", "out of memory", "memory",
    "status", "bad gateway", "http/",
    "ผิดพลาด", "ล้มเหลว", "ขัดข้อง", "ข้อผิดพลาด",
    "file ", "  at ", "caused by:",
)


def looks_suspicious(low: str) -> bool:
    for h in _ERROR_HINTS:
        if h in low:
            return True
    return False


def is_fault_line(line: str) -> bool:
    """True when the line carries a negative-polarity (fault) signal."""
    if not line:
        return False
    if not looks_suspicious(line.lower()):
        return False
    return bool(_ERROR_RE.search(line))
