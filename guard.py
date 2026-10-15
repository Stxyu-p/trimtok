"""trimtok guard: cooperative deadline + fail-open wrapper (Trimtok 2.0 Phase 1).

Safety ceiling, not a latency target: measured reality (2026-10-15) showed a
31 KB compress ranging ~2-45 ms depending on machine load, so the plan's
original 8.5 ms cutoff fail-opened real compressions and cost the priority-2
token reduction. Default is now 50 ms — bounds pathological stalls while
keeping typical outputs compressed; set `guard_ms` (1-1000) per taste.
Python cannot preempt a running call, so Deadline is cooperative: hot loops
tick() every N iterations and a TimeoutError unwinds to the compressor's
fail-open path, which returns the original text unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_BUDGET_MS = 50.0

# Tools whose output must never be compressed (verbatim contract, plan Stage 0).
VERBATIM_TOOLS: frozenset[str] = frozenset({
    "read_file", "view_file", "cat_file", "apply_patch", "edit_file", "trimtok_retrieve",
})


class Deadline:
    """Cooperative deadline; check() raises TimeoutError past the budget."""

    __slots__ = ("deadline_ns",)

    def __init__(self, budget_ms: float = DEFAULT_BUDGET_MS) -> None:
        self.deadline_ns = time.perf_counter_ns() + int(max(0.5, float(budget_ms)) * 1_000_000)

    def time_left_ms(self) -> float:
        return (self.deadline_ns - time.perf_counter_ns()) / 1_000_000.0

    def check(self) -> None:
        if time.perf_counter_ns() >= self.deadline_ns:
            raise TimeoutError("trimtok deadline budget exceeded; triggering fail-open")


@dataclass(slots=True)
class CompressionContext:
    """What a guarded callable sees (mirrors the plan's CompressionContext)."""

    raw_text: str
    tool_name: str = ""
    session_id: str = "default"
    deadline: Deadline = field(default_factory=Deadline)
    meta: dict[str, Any] = field(default_factory=dict)

    def check_deadline(self) -> None:
        self.deadline.check()


def fail_open_guard(budget_ms: float = DEFAULT_BUDGET_MS) -> Callable:
    """Decorator: raw text in -> compressed out; never raises, never inflates.

    Bypasses short text and verbatim tools before spinning up a Deadline.
    func(ctx) must return the compressed string; any exception (including
    TimeoutError) or a non-shrinking result returns raw_text unchanged.
    """

    def decorator(func: Callable[[CompressionContext], str]) -> Callable[..., str]:
        def wrapper(
            raw_text: str,
            tool_name: str = "",
            session_id: str = "default",
            **kwargs: Any,
        ) -> str:
            if (
                not isinstance(raw_text, str)
                or len(raw_text) < 380
                or tool_name in VERBATIM_TOOLS
            ):
                return raw_text
            ctx = CompressionContext(
                raw_text=raw_text,
                tool_name=tool_name,
                session_id=session_id,
                deadline=Deadline(budget_ms),
                meta=kwargs,
            )
            try:
                result = func(ctx)
                return result if isinstance(result, str) and len(result) < len(raw_text) else raw_text
            except Exception:
                return raw_text

        return wrapper

    return decorator
