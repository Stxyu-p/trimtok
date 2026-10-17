"""trimtok signals: unified marker shapes, polarity rules, deadline guards, and BPE token estimators."""

from __future__ import annotations

import json
import re
import sys
import time
from functools import lru_cache
from typing import Any

# Optional accelerators
_TIKTOKEN_AVAILABLE = False
_TOKENIZER: Any = None
try:
    import tiktoken
    _TOKENIZER = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except Exception:
    pass

_MSGSPEC_AVAILABLE = False
try:
    import msgspec
    _MSGSPEC_AVAILABLE = True
except Exception:
    pass

_JITER_AVAILABLE = False
try:
    import jiter
    _JITER_AVAILABLE = True
except Exception:
    pass


# ---------------------------------------------------------------------------
# 1. BPE Token Estimator & Fast JSON
# ---------------------------------------------------------------------------
_SAMPLE_CHARS = 8192
_SAMPLE_THRESHOLD = 65536
_BPE_PAT = re.compile(
    r"'s|'t|'re|'ve|'m|'ll|'d| ?\w+| ?\d+| ?[^\s\w\d]+|\s+(?!\S)|\s+"
)


def fast_json_loads(data: str | bytes) -> Any:
    """Fastest available JSON deserializer (msgspec > jiter > stdlib json)."""
    if _MSGSPEC_AVAILABLE:
        try:
            raw = data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8")
            return msgspec.json.decode(raw)
        except Exception:
            pass
    if _JITER_AVAILABLE:
        try:
            if isinstance(data, str):
                return jiter.from_json(data.encode("utf-8"))
            return jiter.from_json(data)
        except Exception:
            pass
    if isinstance(data, bytes):
        return json.loads(data.decode("utf-8", "replace"))
    return json.loads(data)


def estimate_bpe_tokens(text: str) -> int:
    """Approximate or exact BPE token count. Never raises; 0 for non-str/empty."""
    try:
        if not text or not isinstance(text, str):
            return 0
        if _TOKENIZER is not None:
            if len(text) > _SAMPLE_THRESHOLD:
                sample = text[:_SAMPLE_CHARS]
                count = len(_TOKENIZER.encode_ordinary(sample))
                return max(1, int(len(text) * (count / _SAMPLE_CHARS)))
            return len(_TOKENIZER.encode_ordinary(text))
        if len(text) > _SAMPLE_THRESHOLD:
            sample = text[:_SAMPLE_CHARS]
            ratio = len(_BPE_PAT.findall(sample)) / _SAMPLE_CHARS
            return max(1, int(len(text) * ratio))
        return len(_BPE_PAT.findall(text))
    except Exception:
        return 0


def count_tokens(text: str) -> int:
    """Alias for estimate_bpe_tokens."""
    return estimate_bpe_tokens(text)


def estimate_tokens(text: str) -> int:
    """Standard token estimator interface."""
    return estimate_bpe_tokens(text)


def estimate_bpe_gain(before: str, after: str) -> int:
    """Token gain of a fold (before - after); >0 means the fold is worth it."""
    return estimate_bpe_tokens(before) - estimate_bpe_tokens(after)


def is_tiktoken_available() -> bool:
    return _TIKTOKEN_AVAILABLE


def is_jiter_available() -> bool:
    return _JITER_AVAILABLE


def is_msgspec_available() -> bool:
    return _MSGSPEC_AVAILABLE


# ---------------------------------------------------------------------------
# 2. Polarity & Error Detection
# ---------------------------------------------------------------------------
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


_CACHEABLE_MAX_CHARS = 240
_FAULT_CACHE_SIZE = 16384


def _compute_fault(line: str) -> bool:
    if not line:
        return False
    if not looks_suspicious(line.lower()):
        return False
    return bool(_ERROR_RE.search(line))


_cached_fault = lru_cache(maxsize=_FAULT_CACHE_SIZE)(_compute_fault)


def is_fault_line(line: str) -> bool:
    if not line:
        return False
    if len(line) <= _CACHEABLE_MAX_CHARS:
        return _cached_fault(line)
    return _compute_fault(line)


# ---------------------------------------------------------------------------
# 3. Markers & Sentinels
# ---------------------------------------------------------------------------
SENTINEL_RE = re.compile(
    r"\[(?:@#[0-9A-Za-z]+(?:x\d+)?\]"
    r"|tt:(?:legend\]|(?:terminal|tool|seen|masked|template|shingle|headtail|delta)\b"
    r"[^\]]*(?:retrieve:[0-9a-f]+|#[0-9A-Za-z]+)))"
)

KEEP_PREFIX = "TRIMTOK_KEEP:"

SLOT_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|<TAG>|<UUID>|<SESS>|<TS>|\b\d{1,2}:\d{2}:\d{2}\b|(?<!\d)\d+",
    re.I,
)

LEGEND_FOLD = "[tt:legend] Fold markers: [repeated Nx], [@#ref] (xN = N lines). Full originals in CCR via trimtok_retrieve."
LEGEND_TOON = (
    "[tt:legend] TOON table (lossless): \"@prefix(A)=base\" = shared path "
    "shorthand; \"@rows[N]{f1,f2}:\" header, then N rows, cells split by \"|\" "
    "(quoted = literal)."
)
LEGEND_DELTA = (
    "[tt:legend] Delta vs previous run of the same command: + added, "
    "- resolved/removed, ! still failing; refs = full snapshots via trimtok_retrieve."
)


def is_important(line: str) -> bool:
    """True when a line contains an error, exception, or failure indicator."""
    if not line:
        return False
    if line.startswith(KEEP_PREFIX):
        return True
    return is_fault_line(line)


def delta_head(cmd: str, sim: float, added: int, removed: int, still: int, cur_ref: str, prev_ref: str) -> str:
    sim_pct = int(round(sim * 100)) if sim <= 1.0 else int(sim)
    return (
        f'[tt:delta cmd="{cmd[:60].replace(chr(34), chr(39))}" sim={sim_pct}% '
        f"+#{added} -#{removed} !#{still} {cur_ref} {prev_ref}]"
    )


def terminal_head(kind: str, hidden: int, ref: str) -> str:
    return f"[tt:terminal kind={kind} hidden={hidden} #{ref}]"


def tool_head(saved: int, ref: str) -> str:
    return f"[tt:tool kind=result saved={saved} #{ref}]"


def seen_lines(count: int, ref: str) -> str:
    return f"[@#{ref}x{count}]"


def seen_line(ref: str) -> str:
    return f"[@#{ref}]"


def masked(chars: int, ref: str) -> str:
    return f"[tt:masked chars={chars} #{ref}]"


def template_head(count: int, ref: str) -> str:
    return f"[tt:template lines={count} #{ref}]"


def shingle_head(count: int, ref: str) -> str:
    return f"[tt:shingle #{ref}]"


def headtail_head(kept_head: int, kept_tail: int, omitted: int, ref: str) -> str:
    return f"[tt:headtail omitted={omitted} #{ref}]"


_HASH_RE = re.compile(r"(?:retrieve:|#)([0-9A-Za-z]{1,16})")

_EXEMPT_RE = re.compile(
    r"(?i)port\s+\d{2,5}|(?<!\d):\d{2,5}\b|[0-9a-f]{7,40}|"
    r"\b\d{3,}\s+(errors|lines|files|rows|tests|passed|failed)\b"
)


def is_exempt(line: str) -> bool:
    try:
        if (line or "").startswith("TRIMTOK_KEEP:"):
            return True
        return bool(_EXEMPT_RE.search(line or ""))
    except Exception:
        return False


def find_hashes(text: str) -> list[str]:
    try:
        return _HASH_RE.findall(text or "")
    except Exception:
        return []


def is_compressed(text: str) -> bool:
    try:
        return bool(SENTINEL_RE.search((text or "")[:2000]))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 4. Guard & ANSI Utilities
# ---------------------------------------------------------------------------
VERBATIM_TOOLS = frozenset({"read_file", "write_to_file", "replace_file_content", "ask_question"})

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB0]")


def strip_ansi(text: str) -> str:
    if "\x1b" not in text and "\x9b" not in text:
        return text
    return _ANSI_RE.sub("", text)


class Deadline:
    """Monotonic deadline guard with configurable budget."""

    def __init__(self, budget_ms: float = 50.0) -> None:
        self.budget_ms = max(1.0, budget_ms)
        self.deadline = time.monotonic() + (self.budget_ms / 1000.0)

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise TimeoutError(f"Trimtok deadline exceeded ({self.budget_ms:.1f}ms)")
