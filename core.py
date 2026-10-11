"""trimtok core: deterministic terminal-output compression (Phase 1).

Stdlib only. Pure functions — no external dependencies, no Hermes imports.
Pipeline per SPEC: detect kind -> filter -> token-gate -> error-preservation
quality-gate. Never raises: every entry point catches and returns input.
"""

from __future__ import annotations

import hashlib
import re
import time
from functools import lru_cache

VERSION = "0.5.0"

# Sentinel: compressed output carries [tt:<kind> ...] so subsequent hooks can
# skip it (idempotency). Marker/legend live at HEAD because core truncates tail.
SENTINEL_RE = re.compile(r"\[tt:[a-z]+\b")

# Single compiled patterns, module-level, no per-call re.compile
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB0]")
_ERROR_RE = re.compile(
    r"(?i)\b(error|exception|traceback|failed|failure|panic|fatal|critical|"
    r"warn(?:ing)?|denied|refused|timeout|segfault|assertion)\b"
    r"|^\s*(File |  at |Caused by:)"
)

# Fast pre-filter: single-pass substring scan
_ERROR_HINTS = (
    "error", "exception", "traceback", "fail", "panic", "fatal", "critical",
    "warn", "denied", "refused", "timeout", "segfault", "assert",
    "file ", "  at ", "caused by:",
)


def _looks_suspicious(low: str) -> bool:
    for h in _ERROR_HINTS:
        if h in low:
            return True
    return False


_TS_PREFIX_RE = re.compile(
    r"^\[?\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}[^\]]*\]?\s*"
)
_PID_PREFIX_RE = re.compile(r"^\[?\d+\]?\s*")
_PROGRESS_RE = re.compile(r"^\s*(⠁|⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏|◐|◓|◑|◒|\||/|-|\\|\d+%|\[\s*\d+/\d+\])")
_NOISE_LINE_RE = re.compile(
    r"(?i)^\s*(npm notice|.*deprecated.*|.*audit.* vulnerabilities? \(.*\)|"
    r".*up to date.*|.*already (satisfied|installed).*|^\s*Collecting .*|"
    r"^\s*Downloading .*|^\s*Using cached .*)$"
)
_KEEP_PREFIX = "TRIMTOK_KEEP:"
_HEAD_KEEP = 2
_TAIL_KEEP = 2
_MIN_KEEP = 3

LEGEND = "[tt:legend] Lines folded as [repeated Nx]. Full original in CCR, use trimtok_retrieve."


def strip_ansi(text: str) -> str:
    """Fast ANSI escape code stripper with immediate early-exit check."""
    if "\x1b" not in text and "\x9b" not in text:
        return text
    return _ANSI_RE.sub("", text)


@lru_cache(maxsize=8192)
def _normalize_line(line: str) -> str:
    line = _TS_PREFIX_RE.sub("", line)
    line = _PID_PREFIX_RE.sub("", line)
    return line.strip()


def is_important(line: str) -> bool:
    if line.startswith(_KEEP_PREFIX):
        return True
    if not _looks_suspicious(line.lower()):
        return False
    return bool(_ERROR_RE.search(line))


def detect_kind(text: str) -> str:
    """Cheap content-type router (stdlib only)."""
    head = text[:4000]
    if "diff --git" in head or re.search(r"^@@ -\d+", head, re.M):
        return "diff"
    if re.search(r"(?m)^(FAIL|ERROR|ok|PASSED|FAILED|Traceback|  File )", head):
        return "log"
    if re.search(r"(?m)^[^:\n]{1,120}:\d+:", head):
        return "search"
    try:
        import json as _json
        stripped = head.strip()
        if stripped[:1] in "{[":
            _json.loads(text.strip()[:65536] if len(text) > 65536 else text.strip())
            return "json"
    except Exception:
        pass
    return "text"


def _classify(lines: list[str]) -> list[tuple[str, bool]]:
    """Single pass importance flags: classify once, reuse across stages."""
    out: list[tuple[str, bool]] = []
    for ln in lines:
        if ln.startswith(_KEEP_PREFIX):
            out.append((ln, True))
        elif _looks_suspicious(ln.lower()) and _ERROR_RE.search(ln):
            out.append((ln, True))
        else:
            out.append((ln, False))
    return out


def _fold_repeats(
    flagged: list[tuple[str, bool]],
) -> tuple[list[tuple[str, bool]], int]:
    """Fold adjacent normalized-duplicate runs. Returns ((line, important), hidden)."""
    out: list[tuple[str, bool]] = []
    hidden = 0
    i = 0
    n = len(flagged)
    while i < n:
        line, important = flagged[i]
        if important:
            out.append((line, True))
            i += 1
            continue
        norm = _normalize_line(line)
        j = i + 1
        while j < n and not flagged[j][1] and _normalize_line(flagged[j][0]) == norm:
            j += 1
        run = j - i
        if run >= 3:
            out.append((flagged[i][0], False))
            out.append((f"[repeated {run - 1}x]", False))
            hidden += run - 1
        else:
            out.extend((flagged[k][0], False) for k in range(i, j))
        i = j
    return out, hidden


def _window(
    flagged: list[tuple[str, bool]],
    budget: int,
) -> tuple[list[str], int]:
    """Balanced HEAD/TAIL window with forced error lines. Returns (lines, dropped)."""
    lines = [ln for ln, _ in flagged]
    n = len(lines)
    if n <= budget:
        return lines, 0
    forced_set = {i for i, (_, imp) in enumerate(flagged) if imp}

    # Balanced head and tail: summaries and failure stats live at tail
    head_count = max(_HEAD_KEEP, min(budget // 4, 30))
    tail_count = max(_TAIL_KEEP, min(budget // 3, 40))

    head_indices = set(range(min(head_count, n)))
    tail_indices = set(range(max(0, n - tail_count), n))

    keep_set = forced_set | head_indices | tail_indices
    if len(keep_set) > budget:
        essential = forced_set | {0, max(0, n - 1)}
        if len(essential) <= budget:
            sorted_all = sorted(keep_set)
            keep_set = set(sorted_all[:budget]) | essential
        else:
            keep_set = set(sorted(forced_set)[:budget]) | {0, max(0, n - 1)}
    else:
        rem = budget - len(keep_set)
        if rem > 0:
            for i in range(n):
                if i not in keep_set:
                    keep_set.add(i)
                    rem -= 1
                    if rem == 0:
                        break

    keep = sorted(keep_set)
    out: list[str] = []
    dropped = 0
    prev = -1
    for idx in keep:
        if idx > prev + 1:
            gap = idx - prev - 1
            out.append(f"[... {gap} lines omitted ...]")
            dropped += gap
        out.append(lines[idx])
        prev = idx
    return out, dropped


# Budgets by command verb
_CMD_BUDGETS: tuple[tuple[str, int], ...] = (
    ("diff", 200),
    ("log", 200),
    ("test", 200),
    ("pytest", 200),
    ("grep", 80),
    ("rg", 80),
    ("ls", 60),
    ("git log", 60),
    ("git status", 40),
)


def _budget_for(command: str, kind: str, default: int) -> int:
    cmd = (command or "").lower()
    for verb, budget in _CMD_BUDGETS:
        if verb in cmd:
            return budget
    if kind in ("diff", "log"):
        return 200
    return default


def compress_terminal(
    command: str,
    output: str,
    *,
    min_chars: int = 2048,
    max_lines: int = 120,
    now: float | None = None,
) -> tuple[str, dict]:
    """Compress one terminal output. Returns (new_output, stats).

    stats keys: kind, before_chars, after_chars, saved_chars, hidden_lines,
    dropped_lines, gate (ok|passthrough_small|passthrough_sentinel|
    passthrough_single|reverted_quality|reverted_gate), ms.
    Never raises.
    """
    t0 = time.perf_counter()
    try:
        if not isinstance(output, str):
            return output, {"gate": "passthrough_non_str", "ms": 0.0}
        if len(output) < min_chars:
            return output, {"gate": "passthrough_small", "ms": 0.0}
        if SENTINEL_RE.search(output[:2000]):
            return output, {"gate": "passthrough_sentinel", "ms": 0.0}
        kind = detect_kind(output)
        budget = _budget_for(command, kind, max_lines)
        text = strip_ansi(output)
        lines = text.split("\n")
        # single classification pass; every stage below reuses the flags
        flagged_all = _classify(lines)
        # fold repeats first, reducing lines before regex noise filter
        folded, hidden = _fold_repeats(flagged_all)
        kept = [
            (ln, imp) for ln, imp in folded
            if imp or (not _PROGRESS_RE.match(ln) and not _NOISE_LINE_RE.match(ln))
        ]
        windowed, dropped = _window(kept, budget)
        digest = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:12]
        head = [f"[tt:terminal kind={kind} hidden={hidden + dropped} retrieve:{digest}]", LEGEND]
        new_output = "\n".join(head + windowed)
        # token-gate: never inflate
        if len(new_output) >= len(output):
            return output, {
                "gate": "reverted_gate", "kind": kind,
                "before_chars": len(output), "after_chars": len(output),
                "saved_chars": 0, "ms": (time.perf_counter() - t0) * 1000,
            }
        # quality-gate v1: every error-matching input line class must survive
        in_err = {ln.strip()[:160] for ln, imp in flagged_all if imp}
        out_txt = "\n".join(windowed)
        missing = [e for e in in_err if e and e not in out_txt and not e.startswith(_KEEP_PREFIX)]
        if missing:
            return output, {
                "gate": "reverted_quality", "kind": kind,
                "before_chars": len(output), "after_chars": len(output),
                "saved_chars": 0, "missing_errors": len(missing),
                "ms": (time.perf_counter() - t0) * 1000,
            }
        return new_output, {
            "gate": "ok", "kind": kind, "digest": digest,
            "before_chars": len(output), "after_chars": len(new_output),
            "saved_chars": len(output) - len(new_output),
            "hidden_lines": hidden, "dropped_lines": dropped,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    except Exception:
        return output, {"gate": "passthrough_exception", "ms": (time.perf_counter() - t0) * 1000}
