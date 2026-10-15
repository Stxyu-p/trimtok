"""trimtok delta_engine: cross-turn command snapshot delta (Trimtok 2.0 Phase 4).

Repeated commands (pytest, git status, git diff, ...) usually reproduce the
same output line-for-line. After the first run is snapshotted per
(session, command), a later run whose lines overlap the snapshot by >= the
similarity threshold collapses to a delta view:

    [tt:delta cmd="pytest -q" sim=100% +#2 -#1 !#1 #c8 #c7]
    [tt:legend] Delta vs previous run of the same command: + added, ...
    --- STILL FAILING / ACTIVE ERRORS (1) ---
    ! FAILED test_1000.py::test_boom - AssertionError: expected 1 got 2
    --- ADDED / CHANGED IN THIS TURN (+2) ---
    + FAILED test_4000.py::test_new - boom
    + ===== 2 failed, 1998 passed in 43.2s =====
    --- RESOLVED / REMOVED (-1) ---
    - ===== 1 failed, 1999 passed in 42.1s =====

Fault rail (Priority 1): any line that failed before and still fails is
re-emitted in STILL FAILING even though it is "unchanged" — a test that
failed in turn 1 and still fails in turn 2 must never go blind (plan
canary: Regression Blindness Check). Added lines are ordered faults-first
for the same reason.

State is per (session_id, normalized command), bounded by command / line /
char caps per session. Snapshot refs in the delta head are materialized
lazily at emit time (CCR aliases when the store answers, raw 12-hex
digests otherwise) — a no-delta turn never touches the store. Threshold
defaults to 0.65 (plan kill-criteria: raise to 0.80 if retrieves exceed
~15% of turns). Failure-safe: any error returns None and the caller falls
through to the normal pipeline. Never raises.

Determinism note: compress_terminal stays pure by default — statefulness is
opt-in (`delta_enabled=True`); the terminal hook enables it from config.
"""

from __future__ import annotations

import hashlib
from typing import Any

try:
    from .ccr_v2 import put as _ccr_put
    from .marker import LEGEND_DELTA, delta_head, is_important
except ImportError:  # direct unit-test import
    from ccr_v2 import put as _ccr_put
    from marker import LEGEND_DELTA, delta_head, is_important

MIN_LINES = 12  # smaller outputs never bother
DEFAULT_THRESHOLD = 0.65
_MAX_ADDED = 40
_MAX_REMOVED = 15
_MAX_STILL = 25
_MAX_COMMANDS = 32  # per session
_MAX_SNAPSHOT_LINES = 6000  # per command; larger outputs skip delta
_MAX_SNAPSHOT_CHARS = 400_000  # per command
_MAX_TOTAL_LINES = 24000  # per session
_MAX_TOTAL_CHARS = 1_500_000  # per session


def _digest(text: str) -> str:
    try:
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    except Exception:
        return ""


def _ref_for(text: str) -> str:
    """CCR alias for a snapshot; falls back to the raw 12-hex digest.

    Uses the plugin's own ccr_v2 module instance via the dual-mode import —
    a bare ``import ccr_v2`` inside the packaged plugin would build a second
    module + store instance (extra sqlite connection: leaks a handle into
    tempdir cleanup and can trip transient lock errors into legacy hex refs).
    """
    try:
        ref = _ccr_put(text)
        if isinstance(ref, str) and ref.startswith("#"):
            return ref
    except Exception:
        pass
    d = _digest(text)
    return f"#{d}" if d else ""


class StatefulDeltaEngine:
    def __init__(self) -> None:
        # (session, cmd) -> (raw_text, lines, unique_fault_lines)
        self._state: dict[tuple[str, str], tuple[str, list[str], list[str]]] = {}

    def clear(self, session_id: str = "") -> None:
        sid = str(session_id or "")
        if not sid:
            self._state.clear()
            return
        for key in [k for k in self._state if k[0] == sid]:
            self._state.pop(key, None)

    def _evict(self, sid: str) -> None:
        keys = [k for k in self._state if k[0] == sid]
        while len(keys) > _MAX_COMMANDS:
            self._state.pop(keys.pop(0), None)
        if not keys:
            return
        total_lines = sum(len(v[1]) for k, v in self._state.items() if k[0] == sid)
        total_chars = sum(len(v[0]) for k, v in self._state.items() if k[0] == sid)
        while keys and (total_lines > _MAX_TOTAL_LINES or total_chars > _MAX_TOTAL_CHARS):
            ent = self._state.pop(keys.pop(0), None)
            if ent is not None:
                total_lines -= len(ent[1])
                total_chars -= len(ent[0])

    def try_delta(
        self,
        session_id: str,
        command: str,
        text: str,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        deadline: Any = None,
    ) -> str | None:
        """Delta view of `text` vs the previous run of this command, or None."""
        try:
            if not isinstance(text, str) or not text:
                return None
            if deadline is not None:
                deadline.check()
            lines = [ln.rstrip("\r") for ln in text.split("\n")]
            if (
                len(lines) < MIN_LINES
                or len(lines) > _MAX_SNAPSHOT_LINES
                or len(text) > _MAX_SNAPSHOT_CHARS
            ):
                return None
            sid = str(session_id or "")
            key = (sid, " ".join(str(command or "").split()))
            fault_lines = [ln for ln in dict.fromkeys(lines) if is_important(ln)]
            prev = self._state.pop(key, None)
            self._state[key] = (text, lines, fault_lines)  # refresh LRU position
            self._evict(sid)
            if prev is None:
                return None
            prev_text, prev_lines, _prev_faults = prev
            prev_set = set(prev_lines)
            if deadline is not None:
                deadline.check()
            line_set = set(lines)
            overlap = len(line_set & prev_set)
            sim = overlap / max(len(line_set), 1)
            if sim < threshold:
                return None
            added = [ln for ln in lines if ln not in prev_set]
            removed = [ln for ln in prev_lines if ln not in line_set]
            still = [ln for ln in fault_lines if ln in prev_set]
            # refs materialize lazily: a fire is the only moment the store is touched
            prev_ref = _ref_for(prev_text)
            cur_ref = _ref_for(text)
            body: list[str] = []
            if still:
                body.append(f"--- STILL FAILING / ACTIVE ERRORS ({len(still)}) ---")
                body.extend(f"! {ln}" for ln in still[:_MAX_STILL])
                if len(still) > _MAX_STILL:
                    body.append(f"  .. (+{len(still) - _MAX_STILL} more in {cur_ref}) ..")
            if added:
                # faults first: a newly failing line must not sit below the fold
                fault_set = set(fault_lines)
                added_disp = sorted(added, key=lambda ln: 0 if ln in fault_set else 1)
                body.append(f"--- ADDED / CHANGED IN THIS TURN (+{len(added)}) ---")
                body.extend(f"+ {ln}" for ln in added_disp[:_MAX_ADDED])
                if len(added) > _MAX_ADDED:
                    body.append(f"  .. (+{len(added) - _MAX_ADDED} more in {cur_ref}) ..")
            if removed:
                body.append(f"--- RESOLVED / REMOVED (-{len(removed)}) ---")
                body.extend(f"- {ln}" for ln in removed[:_MAX_REMOVED])
                if len(removed) > _MAX_REMOVED:
                    body.append(f"  .. (-{len(removed) - _MAX_REMOVED} more in {prev_ref}) ..")
            head = delta_head(str(command or ""), sim, len(added), len(removed), len(still), cur_ref, prev_ref)
            return "\n".join([head, LEGEND_DELTA] + body)
        except Exception:
            return None


_ENGINE = StatefulDeltaEngine()


def try_delta(
    session_id: str,
    command: str,
    text: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    deadline: Any = None,
) -> str | None:
    """Module-level facade over the shared engine (used by core/hook)."""
    return _ENGINE.try_delta(session_id, command, text, threshold=threshold, deadline=deadline)


def clear_session(session_id: str = "") -> None:
    """Drop delta state for one session ('' clears everything)."""
    _ENGINE.clear(session_id)
