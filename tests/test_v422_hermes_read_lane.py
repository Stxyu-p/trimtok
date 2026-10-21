"""Hermes-fidelity tests for the v4.2.2 stateful read-file lane.

Production reality (2026-10-21): Hermes' read_file always sends
`offset=1, limit=2000` and returns every line as `LINE|CONTENT`. The v4.2
engine was built against raw payloads, so the lane never fired in the real
runtime: Rule 0 rejected every call, ast.parse() choked on the line-number
prefixes, and the `patch` handler dropped the cached body that the delta lane
diffs against.

These tests simulate the real runtime exactly. Each one was verified to FAIL
against the pre-fix code.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trimtok.dedup import (  # noqa: E402
    clear_session,
    compress_tool_result,
    expire_old_turns,
    note_turn,
)

SID = "hermes_prod"
HERMES_ARGS = {"path": "src/metrics.py", "offset": 1, "limit": 2000}


def _source_lines(n_funcs: int = 12) -> list[str]:
    lines = ["import os", "from typing import Any", "", "GLOBAL_LIMIT: int = 100", ""]
    for idx in range(n_funcs):
        lines.append(f"def compute_metric_{idx}(val: int) -> int:")
        lines.append(f'    """Compute {idx}."""')
        for j in range(16):
            lines.append(f"    x_{j} = {j} * {idx}")
        lines.append(f"    return val + {idx}")
        lines.append("")
    return lines


def _numbered(lines: list[str]) -> str:
    return "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(lines))


def test_hermes_production_numbered_read_patch_reread_lifecycle():
    """read_file(offset=1, limit=2000, LINE|CONTENT) -> patch -> re-read."""
    clear_session(SID)
    raw_lines = _source_lines()
    numbered_v1 = _numbered(raw_lines)

    # Step 1: cold read of a ~6KB file -> verbatim (patch safety).
    out1, st1 = compress_tool_result("read_file", numbered_v1, args=HERMES_ARGS, session_id=SID)
    assert st1["gate"] == "passthrough_reference", st1
    assert out1 == numbered_v1

    # Step 2: re-read unchanged -> outline stub, not another full body.
    out2, st2 = compress_tool_result("read_file", numbered_v1, args=HERMES_ARGS, session_id=SID)
    assert st2["gate"] == "ok" and st2["kind"] == "file_cache_hit", st2
    assert "def compute_metric_0" in out2
    assert st2["saved_chars"] > 0

    # Step 3: the agent patches the file (all later LINE| numbers shift).
    compress_tool_result("patch", "Successfully applied patch to src/metrics.py",
                         args={"path": "src/metrics.py"}, session_id=SID)
    edited = list(raw_lines)
    edited[48:48] = ["    patched_bonus = 9999", "    val += patched_bonus"]
    numbered_v2 = _numbered(edited)

    # Step 4: re-read after the patch -> contextual delta (the cache survived).
    out3, st3 = compress_tool_result("read_file", numbered_v2, args=HERMES_ARGS, session_id=SID)
    assert st3["gate"] == "ok" and st3["kind"] == "file_delta", st3
    assert "patched_bonus = 9999" in out3
    assert st3["saved_chars"] > int(len(numbered_v2) * 0.30), st3


def test_hermes_targeted_slice_stays_verbatim():
    """offset=142, limit=77 is a deliberate slice -> byte-identical passthrough."""
    clear_session(SID)
    lines = _source_lines(40)
    numbered = _numbered(lines)
    out, st = compress_tool_result(
        "read_file", numbered,
        args={"path": "src/metrics.py", "offset": 142, "limit": 77},
        session_id=SID)
    assert st["gate"] == "passthrough_reference", st
    assert out == numbered


def test_hermes_read_file_extra_uses_the_engine():
    """read_file_extra is the same payload shape and must not be dropped."""
    clear_session(SID)
    big = _numbered(_source_lines() * 3)
    out, st = compress_tool_result("read_file_extra", big, args=HERMES_ARGS, session_id=SID)
    assert st["gate"] == "ok" and st["kind"] == "file_skeleton", st
    assert st["saved_chars"] > int(len(big) * 0.45), st
    # every kept line must still carry its ORIGINAL line number
    assert "1|import os" in out


def test_hermes_multi_step_reread_survives_turn_expiry():
    """A read -> search -> terminal -> read loop must still hit the cache."""
    clear_session(SID)
    numbered = _numbered(_source_lines())
    _, st1 = compress_tool_result("read_file", numbered, args=HERMES_ARGS, session_id=SID)
    digest = st1.get("digest", "")
    for i in range(6):
        note_turn(numbered, digest, session_id=SID, turn_id=f"t{i}")
        expire_old_turns(SID, keep_turns=2)
    out, st = compress_tool_result("read_file", numbered, args=HERMES_ARGS, session_id=SID)
    assert st["gate"] == "ok" and st["kind"] == "file_cache_hit", st
    assert st["saved_chars"] > int(len(numbered) * 0.5), st
