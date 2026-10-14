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

VERSION = "0.6.2"

try:
    from .marker import KEEP_PREFIX as _KEEP_PREFIX
    from .marker import LEGEND_FOLD as LEGEND
    from .marker import SENTINEL_RE, SLOT_RE as _SLOT_RE, estimate_tokens, is_exempt, is_important, terminal_head
except ImportError:  # direct unit-test import
    from marker import KEEP_PREFIX as _KEEP_PREFIX
    from marker import LEGEND_FOLD as LEGEND
    from marker import SENTINEL_RE, SLOT_RE as _SLOT_RE, estimate_tokens, is_exempt, is_important, terminal_head

# Single compiled patterns, module-level, no per-call re.compile
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB0]")
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

# Fast pre-filter: single-pass substring scan
_ERROR_HINTS = (
    "error", "exception", "traceback", "fail", "panic", "fatal", "critical",
    "warn", "denied", "refused", "timeout", "segfault", "segment", "fault",
    "assert", "oom", "epipe", "exit code", "out of memory", "memory",
    "ผิดพลาด", "ล้มเหลว", "ขัดข้อง", "ข้อผิดพลาด",
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
# (_KEEP_PREFIX + _SLOT_RE imported from marker: single source of truth.)
_HEAD_KEEP = 2
_TAIL_KEEP = 2


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


@lru_cache(maxsize=8192)
def _template_key(line: str) -> str:
    """Template key with volatile slots normalized; '' = not groupable."""
    norm = _normalize_line(line)
    if len(norm) < 20:
        return ""
    key = _SLOT_RE.sub("{}", norm)
    if len(key) < 20:
        return ""
    return key


def _fold_templates(
    flagged: list[tuple[str, bool]],
) -> tuple[list[tuple[str, bool]], int, int]:
    """Fold scattered same-template runs. Returns (flagged, hidden, groups).

    Adjacent-repeat folding cannot fire when template lines interleave with
    other content, so group by template key instead of position. First
    occurrence stays verbatim (keep-earliest), rest collapse into one
    [template ×N] marker. Important lines never group. Groups of <3 pass
    through (same threshold as adjacent runs).
    """
    groups: dict[str, list[int]] = {}
    for idx, (line, important) in enumerate(flagged):
        if important or is_exempt(line):
            continue
        key = _template_key(line)
        if key:
            groups.setdefault(key, []).append(idx)
    big = {k: v for k, v in groups.items() if len(v) >= 3}
    if not big:
        return flagged, 0, 0
    first_of = {}
    for key, members in big.items():
        first_of[members[0]] = (key, len(members))
    in_big = set()
    for members in big.values():
        in_big.update(members[1:])
    out: list[tuple[str, bool]] = []
    hidden = 0
    for idx, (line, important) in enumerate(flagged):
        if idx in in_big:
            hidden += 1
            continue
        out.append((line, important))
        if idx in first_of:
            _, count = first_of[idx]
            out.append((f"[template ×{count - 1}]", False))
    return out, hidden, len(big)


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
    return [(line, is_important(line)) for line in lines]


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
        if important or is_exempt(line):
            out.append((line, important))
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
    # ponytail: word-boundary verb match; substring matched "cat diffutils.txt"->diff, "false"->ls.
    # Two-word verbs first so "git log" hits its own entry before bare "log".
    cmd = (command or "").lower()
    tokens = re.split(r"[^a-z0-9]+", cmd)
    words = {t for t in tokens if t}
    for verb, budget in _CMD_BUDGETS:
        parts = verb.split()
        if len(parts) == 2 and parts[0] in words and parts[1] in words:
            return budget
    for verb, budget in _CMD_BUDGETS:
        if " " not in verb and verb in words:
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
        # Drain templates second (scattered same-shape lines adjacent folding
        # cannot see); groups <3 pass through, important lines never group
        folded, t_hidden, _t_groups = _fold_templates(folded)
        hidden += t_hidden
        kept = [
            (ln, imp) for ln, imp in folded
            if imp or (not _PROGRESS_RE.match(ln) and not _NOISE_LINE_RE.match(ln))
        ]
        windowed, dropped = _window(kept, budget)
        digest = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:12]
        head = [terminal_head(kind, hidden + dropped, digest), LEGEND]
        new_output = "\n".join(head + windowed)
        # token-gate: never inflate
        if len(new_output) >= len(output):
            return output, {
                "gate": "reverted_gate", "kind": kind,
                "before_chars": len(output), "after_chars": len(output),
                "saved_chars": 0, "ms": (time.perf_counter() - t0) * 1000,
            }
        # quality-gate v2: every error-matching input line class must survive,
        # unless forced lines overflow the budget (W1). In overflow, survival
        # is impossible — reverting to 0% hides the savings while showing the
        # same truncated view anyway. Accept the windowed output as partial:
        # gap markers + head hidden count already report the omission, and the
        # full original stays in CCR. Stats carry partial=True + omitted count.
        in_err = {ln.strip()[:160] for ln, imp in flagged_all if imp}
        out_txt = "\n".join(windowed)
        missing = [e for e in in_err if e and e not in out_txt and not e.startswith(_KEEP_PREFIX)]
        if missing:
            forced_total = sum(1 for _, imp in flagged_all if imp)
            if forced_total > budget:
                return new_output, {
                    "gate": "ok", "kind": kind, "digest": digest,
                    "before_chars": len(output), "after_chars": len(new_output),
                    "saved_chars": len(output) - len(new_output),
                    "est_tokens_before": estimate_tokens(output),
                    "est_tokens_after": estimate_tokens(new_output),
                    "hidden_lines": hidden, "dropped_lines": dropped,
                    "partial": True, "omitted_errors": len(missing),
                    "ms": (time.perf_counter() - t0) * 1000,
                }
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
            "est_tokens_before": estimate_tokens(output),
            "est_tokens_after": estimate_tokens(new_output),
            "hidden_lines": hidden, "dropped_lines": dropped,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    except Exception:
        return output, {"gate": "passthrough_exception", "ms": (time.perf_counter() - t0) * 1000}
