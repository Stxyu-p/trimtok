"""trimtok core: deterministic terminal-output compression (Phase 1 & Trimtok 4.0).

Stdlib + optional rapidfuzz (fuzzy fold) + optional tiktoken (token estimation).
Pipeline per SPEC: detect kind -> filter -> token-gate -> error-preservation
quality-gate. Never raises: every entry point catches and returns input.
"""

from __future__ import annotations

import hashlib
import re
import time
from functools import lru_cache
from typing import Any

# Optional fuzzy matcher (rapidfuzz is a declared dependency; difflib fallback).
try:
    from rapidfuzz.distance import Levenshtein as _Levenshtein
    _FUZZY_AVAILABLE = True
except Exception:
    _Levenshtein = None
    _FUZZY_AVAILABLE = False

VERSION = "5.2.0"

try:
    from . import delta_engine
    from .signals import (
        KEEP_PREFIX as _KEEP_PREFIX,
        LEGEND_FOLD as LEGEND,
        SENTINEL_RE,
        SLOT_RE as _SLOT_RE,
        Deadline,
        estimate_tokens,
        fast_json_loads,
        is_exempt,
        is_important,
        _VALUE_LINE_RE,
        strip_ansi,
        adaptive_terminal_head,
        terminal_head,
        score_anchor_preservation,
    )
    from .pruners import prune_traceback, prune_unified_diff, collapse_success_output
    from .ccr import put as _ccr_put
except (ImportError, ValueError):
    from trimtok import delta_engine
    from trimtok.signals import (
        KEEP_PREFIX as _KEEP_PREFIX,
        LEGEND_FOLD as LEGEND,
        SENTINEL_RE,
        SLOT_RE as _SLOT_RE,
        Deadline,
        estimate_tokens,
        fast_json_loads,
        is_exempt,
        is_important,
        _VALUE_LINE_RE,
        strip_ansi,
        adaptive_terminal_head,
        terminal_head,
        score_anchor_preservation,
    )
    from trimtok.pruners import prune_traceback, prune_unified_diff, collapse_success_output
    from trimtok.ccr import put as _ccr_put

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB0]")

_TS_PREFIX_RE = re.compile(
    r"^\[?\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}[^\]]*\]?\s*"
)
_PID_PREFIX_RE = re.compile(r"^\[\d+\]\s*|^pid[=:]\s*\d+\s*", re.I)
_PROGRESS_RE = re.compile(r"^\s*(⠁|⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏|◐|◓|◑|◒|\||/|-|\\|\d+%|\[\s*\d+/\d+\])")
_NOISE_LINE_RE = re.compile(
    r"(?i)^\s*(npm notice|.*deprecated.*|.*audit.* vulnerabilities? \(.*\)|"
    r".*up to date.*|.*already (satisfied|installed).*|^\s*Collecting .*|"
    r"^\s*Downloading .*|^\s*Using cached .*)$"
)
_HEAD_KEEP = 2
_TAIL_KEEP = 2


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
    # Distinct port/config values must not collapse into one template key
    # (would hide differing values in context). Skip grouping for such lines.
    if _VALUE_LINE_RE.search(line or ""):
        return ""
    key = _SLOT_RE.sub("{}", norm)
    if len(key) < 20:
        return ""
    return key


def _fold_templates(
    flagged: list[tuple[str, bool]],
    deadline: Any | None = None,
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
        if deadline is not None and (idx & 511) == 0:
            deadline.check()
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


def detect_kind(text: str) -> str:
    """Cheap content-type router inspecting head and tail for late failure summaries."""
    sample = text[:4000] if len(text) <= 8000 else (text[:4000] + "\n" + text[-4000:])
    if "diff --git" in sample or re.search(r"^@@ -\d+", sample, re.M):
        return "diff"
    if re.search(r"(?m)^(FAIL|ERROR|ok|PASSED|FAILED|Traceback|  File |panic:|thread '.*' panicked)", sample):
        return "log"
    if re.search(r"(?m)^[^:\n]{1,120}:\d+:", text[:4000]):
        return "search"
    try:
        stripped = text.strip()
        if len(stripped) >= 2 and (
            (stripped[0] == "{" and stripped[-1] == "}") or
            (stripped[0] == "[" and stripped[-1] == "]")
        ):
            if len(stripped) <= 65536:
                fast_json_loads(stripped)
            return "json"
    except Exception:
        pass
    return "text"


def _classify(lines: list[str]) -> list[tuple[str, bool]]:
    """Single pass importance flags: classify once, reuse across stages."""
    return [(line, is_important(line)) for line in lines]


def _fold_repeats(
    flagged: list[tuple[str, bool]],
    deadline: Any | None = None,
) -> tuple[list[tuple[str, bool]], int]:
    """Fold adjacent normalized-duplicate runs. Returns ((line, important), hidden)."""
    out: list[tuple[str, bool]] = []
    hidden = 0
    i = 0
    n = len(flagged)
    while i < n:
        if deadline is not None and (i & 511) == 0:
            deadline.check()
        line, important = flagged[i]
        if important or is_exempt(line):
            out.append((line, important))
            i += 1
            continue
        norm = _normalize_line(line)
        j = i + 1
        # Fuzzy fold: lines whose normalized form is >= 0.85 similar are
        # treated as the same template (handles variation like "request 123"
        # vs "request 124"). Falls back to exact match when rapidfuzz absent.
        # Guard: only fuzzy on short lines (<200 chars) to limit O(n^2) cost.
        _do_fuzzy = _FUZZY_AVAILABLE and len(norm) < 200
        while j < n and not flagged[j][1]:
            other_norm = _normalize_line(flagged[j][0])
            if len(other_norm) >= 200:
                # Long lines: exact match only (performance guard).
                if other_norm != norm:
                    break
                j += 1
                continue
            if _do_fuzzy:
                sim = _Levenshtein.normalized_similarity(norm, other_norm)
            else:
                import difflib
                sim = difflib.SequenceMatcher(None, norm, other_norm).ratio()
            if sim < 0.85:
                break
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
            # Fault-dense payload: keeping the FIRST `budget` faults silently throws
            # away every later error. Sample the fault set evenly and always keep
            # its tail (the failure that ended the run lives there).
            faults = sorted(forced_set)
            tail_n = min(max(_TAIL_KEEP, budget // 4), len(faults))
            head_n = max(1, budget - tail_n)
            step = max(1, len(faults) // head_n)
            keep_set = set(faults[::step][:head_n]) | set(faults[-tail_n:]) | {0, max(0, n - 1)}
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
    session_id: str = "",
    delta_enabled: bool = False,
    delta_threshold: float = 0.65,
    deadline: Any | None = None,
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
        if deadline is not None and len(output) > 100_000:
            # A large payload legitimately needs more CPU than a tiny one: the guard
            # exists to bound pathological work, not to punish size. Killing a 435KB
            # log at 200ms shipped it raw (0% saved) for the sake of 22ms of folding.
            _remain = max(0.0, (getattr(deadline, "deadline", 0.0) - time.monotonic()) * 1000.0)
            _base = getattr(deadline, "budget_ms", 200.0)
            _scaled = _base + (len(output) - 100_000) / 1200.0
            if _scaled > _remain:
                deadline = Deadline(min(_base * 3.0, max(_scaled, _remain)))
        if session_id and command:
            # A shell command that rewrites files invalidates the cached body for
            # the path it touched -- otherwise the next read_file would claim the
            # file is "unchanged" when it is not.
            try:
                from .dedup import invalidate_shell_mutations
            except (ImportError, ValueError):
                from trimtok.dedup import invalidate_shell_mutations
            try:
                invalidate_shell_mutations(command, session_id)
            except Exception:
                pass
        if command and len(output) >= 256:
            # Package manager / clean build outputs collapse to 1-line factsheet
            clean_success = collapse_success_output(command, output)
            if clean_success != output:
                tb = estimate_tokens(output)
                ta = estimate_tokens(clean_success)
                return clean_success, {
                    "gate": "ok", "kind": "success_collapse", "digest": "",
                    "before_chars": len(output), "after_chars": len(clean_success),
                    "saved_chars": len(output) - len(clean_success),
                    "est_tokens_before": tb, "est_tokens_after": ta,
                    "hidden_lines": output.count("\n"), "dropped_lines": 0,
                    "ms": (time.perf_counter() - t0) * 1000,
                }
        if len(output) < min_chars:
            # v4.2 Tier 0 for the 512..min_chars band: ANSI + hygiene are lossless,
            # so ship the cleaned text with no header at all instead of passing the
            # raw bytes through (telemetry: 1,306 calls sat in 1K-2K with 0 saving).
            if len(output) >= 512 and not SENTINEL_RE.search(output[:512]):
                try:
                    kind_small = detect_kind(output)
                    clean = strip_ansi(output)
                    if kind_small != "diff":
                        # A unified diff carries " " on its blank context lines --
                        # hygiene would strip that space and corrupt the diff.
                        try:
                            from .dedup import hygiene
                        except (ImportError, ValueError):
                            from trimtok.dedup import hygiene
                        clean, _ = hygiene(clean.rstrip())
                    folded_small, hidden_small = _fold_repeats(_classify(clean.split("\n")), deadline)
                    if hidden_small >= 3:
                        # Repeated lines: fold them and mark the omission.
                        digest_s = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:12]
                        body_s = "\n".join(ln for ln, _ in folded_small)
                        head_s = adaptive_terminal_head(
                            kind_small, hidden_small, digest_s, len(output),
                            len(output) - len(body_s), LEGEND, force_tier=1)
                        cand_s = "\n".join(head_s + [body_s]) if head_s else body_s
                        tb, ta = estimate_tokens(output), estimate_tokens(cand_s)
                        if len(cand_s) + 40 <= len(output) and ta < tb:
                            return cand_s, {
                                "gate": "ok", "kind": kind_small, "digest": digest_s,
                                "before_chars": len(output), "after_chars": len(cand_s),
                                "saved_chars": len(output) - len(cand_s),
                                "est_tokens_before": tb, "est_tokens_after": ta,
                                "hidden_lines": hidden_small, "dropped_lines": 0,
                                "ms": (time.perf_counter() - t0) * 1000,
                            }
                    if len(clean) < len(output):
                        tb, ta = estimate_tokens(output), estimate_tokens(clean)
                        if ta < tb:
                            return clean, {
                                "gate": "ok", "kind": kind_small, "lossless_tier0": True,
                                "before_chars": len(output), "after_chars": len(clean),
                                "saved_chars": len(output) - len(clean),
                                "est_tokens_before": tb, "est_tokens_after": ta,
                                "hidden_lines": 0, "dropped_lines": 0,
                                "ms": (time.perf_counter() - t0) * 1000,
                            }
                except Exception:
                    pass
            return output, {"gate": "passthrough_small", "reason": "below_min_chars", "ms": 0.0}
        if SENTINEL_RE.search(output[:2000]):
            return output, {"gate": "passthrough_sentinel", "reason": "sentinel", "ms": 0.0}
        if deadline is not None:
            deadline.check()
        kind = detect_kind(output)
        budget = _budget_for(command, kind, max_lines)
        text = strip_ansi(output)

        if delta_enabled:
            _delta = delta_engine.try_delta(
                session_id, command, text,
                threshold=delta_threshold, deadline=deadline,
            )
            if _delta is not None and len(_delta) < len(output):
                return _delta, {
                    "gate": "ok", "kind": kind, "delta": True,
                    "before_chars": len(output), "after_chars": len(_delta),
                    "saved_chars": len(output) - len(_delta),
                    "est_tokens_before": estimate_tokens(output),
                    "est_tokens_after": estimate_tokens(_delta),
                    "digest": hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:12],
                    "ms": (time.perf_counter() - t0) * 1000,
                }
        if kind == "diff":
            text = prune_unified_diff(text, budget=0)
        text = prune_traceback(text)
        lines = text.split("\n")
        flagged_all = _classify(lines)
        folded, hidden = _fold_repeats(flagged_all, deadline)
        folded, t_hidden, _t_groups = _fold_templates(folded, deadline)
        hidden += t_hidden
        kept = [
            (ln, imp) for ln, imp in folded
            if imp or (not _PROGRESS_RE.match(ln) and not _NOISE_LINE_RE.match(ln))
        ]
        windowed, dropped = _window(kept, budget)
        digest = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()[:12]
        try:
            _ccr_put(output)
        except Exception:
            pass
        # Faults the window could not keep must be announced in the header, not
        # silently dropped: the #ref restores them, but only if the reader knows.
        # Count by multiset, don't substring-search: a set loses duplicate fault lines
        # (5 identical errors, 1 kept -> 4 hidden but reported 0), and `needle not in
        # out_txt` over a 400KB payload blew the guard budget.
        fault_counts: dict[str, int] = {}
        for _ln, _imp in flagged_all:
            if _imp and _ln and not _ln.startswith(_KEEP_PREFIX):
                fault_counts[_ln] = fault_counts.get(_ln, 0) + 1
        for _ln in windowed:
            if fault_counts.get(_ln):
                fault_counts[_ln] -= 1
        n_missing_faults = sum(fault_counts.values())
        omitted_total = hidden + dropped
        gross_saved = len(output) - sum(len(x) + 1 for x in windowed)
        tokens_before = estimate_tokens(output)
        head = adaptive_terminal_head(kind, omitted_total, digest, len(output), gross_saved, LEGEND,
                                      errors_hidden=n_missing_faults)
        new_output = "\n".join(head + windowed) if head else "\n".join(windowed)
        tokens_after = estimate_tokens(new_output)
        if len(new_output) >= len(output) or tokens_after >= tokens_before:
            # v4.2 cascade rescue: a header that eats the saving IS the 2KB-4KB
            # reverted_gate band (558 telemetry events). Try the nano header, then
            # lossless-only ANSI/hygiene strip, before giving up on the payload.
            #
            # ponytail: no retry budget / no loop -- two fixed fallbacks, because a
            # third tier has never paid for its own header in the telemetry.
            if omitted_total > 0:
                nano = adaptive_terminal_head(
                    kind, omitted_total, digest, len(output), gross_saved, LEGEND, force_tier=1,
                    errors_hidden=n_missing_faults)
                nano_out = "\n".join(nano + windowed) if nano else "\n".join(windowed)
                nano_tok = estimate_tokens(nano_out)
                if len(nano_out) < len(output) and nano_tok < tokens_before:
                    new_output, tokens_after = nano_out, nano_tok
            if len(new_output) >= len(output) or tokens_after >= tokens_before:
                clean_only = strip_ansi(output).rstrip()
                clean_tok = estimate_tokens(clean_only)
                if len(clean_only) + 12 <= len(output) and clean_tok < tokens_before:
                    return clean_only, {
                        "gate": "ok", "kind": kind, "lossless_tier0": True,
                        "before_chars": len(output), "after_chars": len(clean_only),
                        "saved_chars": len(output) - len(clean_only),
                        "est_tokens_before": tokens_before, "est_tokens_after": clean_tok,
                        "hidden_lines": 0, "dropped_lines": 0,
                        "ms": (time.perf_counter() - t0) * 1000,
                    }
                return output, {
                    "gate": "reverted_gate", "kind": kind,
                    "before_chars": len(output), "after_chars": len(output),
                    "saved_chars": 0, "ms": (time.perf_counter() - t0) * 1000,
                }
        if n_missing_faults:
            # Emit partial (windowed output + omitted count) instead of reverting
            # to raw: keeps compression benefit while flagging lost errors for
            # retrieval via #ref (F2). Revert only if windowed output is empty.
            if not new_output:
                return output, {
                    "gate": "reverted_quality", "kind": kind,
                    "before_chars": len(output), "after_chars": len(output),
                    "saved_chars": 0, "missing_errors": n_missing_faults,
                    "ms": (time.perf_counter() - t0) * 1000,
                }
            return new_output, {
                "gate": "ok", "kind": kind, "digest": digest,
                "before_chars": len(output), "after_chars": len(new_output),
                "saved_chars": len(output) - len(new_output),
                "est_tokens_before": tokens_before,
                "est_tokens_after": tokens_after,
                "hidden_lines": hidden, "dropped_lines": dropped,
                "partial": True, "omitted_errors": n_missing_faults,
                "ms": (time.perf_counter() - t0) * 1000,
            }
        saved_chars = len(output) - len(new_output)
        if saved_chars >= 0.80 * len(output) and kind not in ("log", "repeat"):
            # Guard against over-compression destroying navigation anchors on code/diff/test outputs
            anchor_score = score_anchor_preservation(output, new_output)
            if anchor_score < 0.70 and len(output) < 50_000:
                return output, {
                    "gate": "reverted_gate", "kind": kind, "reason": "low_anchor_preservation",
                    "before_chars": len(output), "after_chars": len(output),
                    "saved_chars": 0, "anchor_score": anchor_score, "ms": (time.perf_counter() - t0) * 1000,
                }
        return new_output, {
            "gate": "ok", "kind": kind, "digest": digest,
            "before_chars": len(output), "after_chars": len(new_output),
            "saved_chars": saved_chars,
            "est_tokens_before": tokens_before,
            "est_tokens_after": tokens_after,
            "hidden_lines": hidden, "dropped_lines": dropped,
            "ms": (time.perf_counter() - t0) * 1000,
        }
    except TimeoutError:
        # The budget died mid-fold. If the payload is huge (>20KB), stashing in CCR
        # and shipping head+tail avoids blowing the context window with raw bytes.
        try:
            if len(output) >= 20_000:
                _ref = _ccr_put(output)
                lines = output.splitlines()
                n_total = len(lines)
                if n_total > 80:
                    kept_head = lines[:30]
                    kept_tail = lines[-30:]
                    omitted = n_total - 60
                    ref_tag = f" #{_ref}" if _ref else ""
                    stub = (
                        "\n".join(kept_head)
                        + f"\n[tt:timeout_bounded{ref_tag} ({omitted} lines omitted under deadline) -> trimtok_retrieve]\n"
                        + "\n".join(kept_tail)
                    )
                    return stub, {
                        "gate": "ok", "kind": "timeout_bounded", "digest": _ref or "",
                        "degraded": "deadline", "before_chars": len(output),
                        "after_chars": len(stub), "saved_chars": len(output) - len(stub),
                        "hidden_lines": omitted, "dropped_lines": 0,
                        "ms": (time.perf_counter() - t0) * 1000,
                    }
            clean = strip_ansi(output).rstrip()
            if len(clean) + 12 <= len(output):
                return clean, {
                    "gate": "ok", "kind": detect_kind(output), "lossless_tier0": True,
                    "degraded": "deadline",
                    "before_chars": len(output), "after_chars": len(clean),
                    "saved_chars": len(output) - len(clean),
                    "est_tokens_before": 0, "est_tokens_after": 0,
                    "ms": (time.perf_counter() - t0) * 1000,
                }
        except Exception:
            pass
        return output, {"gate": "passthrough_timeout", "reason": "deadline", "ms": (time.perf_counter() - t0) * 1000}
    except Exception as _e:
        return output, {"gate": "passthrough_exception", "reason": f"{type(_e).__name__}: {_e}"[:200],
                        "ms": (time.perf_counter() - t0) * 1000}
