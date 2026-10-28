"""Anti-lossy context compaction handler that stashes old tool results into CCR."""

from __future__ import annotations

from typing import Any
from .ccr import put as ccr_put

def stash_old_tool_results(
    messages: list[dict[str, Any]],
    preserve_recent_turns: int = 3,
    min_chars: int = 200,
) -> list[dict[str, Any]]:
    # Preserve conversational reasoning while offloading verbose intermediate tool data to disk
    if not messages:
        return []

    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") in ("tool", "function") or "tool_call_id" in m
    ]
    preserve_set = set(tool_indices[-preserve_recent_turns:]) if preserve_recent_turns > 0 else set()

    result = []
    for i, msg in enumerate(messages):
        if i in preserve_set or i not in tool_indices:
            result.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, str) or len(content) < min_chars:
            result.append(msg)
            continue

        ref = ccr_put(content)
        if not ref:
            result.append(msg)
            continue

        lines = content.count("\n") + 1
        bytes_len = len(content.encode("utf-8"))
        stashed_msg = dict(msg)
        stashed_msg["content"] = f"[tt:stashed {ref} ({lines} lines, {bytes_len} B) -> trimtok_retrieve \"{ref}\"]"
        result.append(stashed_msg)

    return result
