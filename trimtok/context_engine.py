"""Native Hermes ContextEngine implementation for trimtok."""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("trimtok.context_engine")

try:
    from agent.context_engine import ContextEngine
except ImportError:
    # Standalone fallback when running outside of hermes-agent host environment
    class ContextEngine:  # type: ignore[no-redef]
        """Minimal fallback ABC matching Hermes agent.context_engine.ContextEngine."""

        threshold_percent: float = 0.50
        protect_first_n: int = 3
        protect_last_n: int = 6


class TrimtokContextEngine(ContextEngine):
    """Trimtok native context engine providing deterministic zero-loss context compaction."""

    def __init__(
        self,
        context_length: int = 128_000,
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        protect_last_n: int = 6,
        min_stash_chars: int = 2048,
        fault_scan_lines: int = 2000,
    ) -> None:
        self.context_length = int(context_length)
        self.threshold_percent = float(threshold_percent)
        self.protect_first_n = int(protect_first_n)
        self.protect_last_n = int(protect_last_n)
        self.threshold_tokens = int(self.context_length * self.threshold_percent)
        # A stash costs the agent a retrieval turn, so only results big enough to
        # repay it move. Below this the stub is longer than the saving.
        self.min_stash_chars = int(min_stash_chars)
        self.fault_scan_lines = int(fault_scan_lines)

        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0
        self.last_total_tokens: int = 0
        self.compression_count: int = 0

    @property
    def name(self) -> str:
        return "trimtok"

    def update_from_response(self, usage: dict[str, Any]) -> None:
        if not isinstance(usage, dict):
            return
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or 0)
        self.last_total_tokens = int(usage.get("total_tokens") or 0)

    def should_compress(self, prompt_tokens: Optional[int] = None) -> bool:
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        if self.threshold_tokens <= 0:
            return False
        return tokens >= self.threshold_tokens

    def update_model(self, model: str, context_length: int, **kwargs: Any) -> None:
        self.context_length = int(context_length)
        self.threshold_tokens = int(self.context_length * self.threshold_percent)

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Compact conversation history by stashing middle tool results to CCR."""
        if not messages:
            return []

        has_system = messages[0].get("role") == "system"
        head_boundary = (1 if has_system else 0) + self.protect_first_n
        tail_boundary = max(head_boundary, len(messages) - self.protect_last_n)

        if head_boundary >= tail_boundary:
            return [m.copy() for m in messages]

        from trimtok import ccr

        out: list[dict[str, Any]] = []
        stashed = 0
        for i, msg in enumerate(messages):
            if head_boundary <= i < tail_boundary and msg.get("role") == "tool":
                content = msg.get("content", "")
                if self._should_stash(content):
                    ref = ccr.put(content)
                    if ref:
                        lines = content.count("\n") + 1
                        tool_name = str(msg.get("name") or msg.get("tool_name") or "").strip()
                        label = f"{tool_name} result" if tool_name else "tool result"
                        preview = self._preview_line(content)
                        stub = f"[tt:stashed {label} ({lines} lines, {len(content):,} chars) · trimtok_retrieve '{ref}']"
                        if preview:
                            stub += f"\nPreview: {preview}"
                        out.append({**msg, "content": stub})
                        stashed += 1
                        continue
            out.append(msg.copy())

        if stashed:
            self.compression_count += 1
        return out

    @staticmethod
    def _preview_line(content: str, max_chars: int = 80) -> str:
        """Extract a compact first-line hint so the agent knows what #ref contains."""
        for raw_ln in content.splitlines()[:8]:
            s = " ".join(raw_ln.strip().split())
            if not s or s.startswith("[tt:legend]"):
                continue
            if len(s) > max_chars:
                return s[: max_chars - 3] + "..."
            return s
        return ""

    def _should_stash(self, content: Any) -> bool:
        """Only a large, already-stashed-free, fault-free tool result is worth a round trip.

        A result the agent is debugging (a traceback it just caused) must stay
        readable: stashing it hides the one line it needs and costs a retrieval.
        """
        if not isinstance(content, str) or len(content) < self.min_stash_chars:
            return False
        if content.startswith("[tt:stashed"):
            return False
        try:
            from .signals import is_important
        except ImportError:
            from trimtok.signals import is_important
        for idx, line in enumerate(content.splitlines()):
            if idx >= self.fault_scan_lines:
                return True
            if is_important(line):
                return False
        return True

    def clone_for_agent(self) -> TrimtokContextEngine:
        """Create an isolated engine instance for an agent/subagent."""
        cloned = TrimtokContextEngine(
            context_length=self.context_length,
            threshold_percent=self.threshold_percent,
            protect_first_n=self.protect_first_n,
            protect_last_n=self.protect_last_n,
            min_stash_chars=self.min_stash_chars,
            fault_scan_lines=self.fault_scan_lines,
        )
        cloned.threshold_tokens = self.threshold_tokens
        cloned.last_prompt_tokens = self.last_prompt_tokens
        cloned.last_completion_tokens = self.last_completion_tokens
        cloned.last_total_tokens = self.last_total_tokens
        return cloned


