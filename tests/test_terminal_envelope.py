"""Terminal results arrive as a JSON envelope; the inner text must still be compressed.

Without unwrapping, the JSON lane minifies one long line and the terminal lane
never sees the 1.5 MB of log text that dominates tool traffic.
"""
import json
import os
import re
import tempfile

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="trimtok-envelope-"))

import trimtok as plugin
from trimtok import ccr
from trimtok.dedup import _terminal_envelope


class _ConfigValues:
    def __init__(self, values):
        self.values = {**values, "usage_enabled": False}

    def get_config(self, key, default):
        return self.values.get(key, default)


def _log(lines: int = 400) -> str:
    return "".join(
        f"2026-11-06 10:00:{i % 60:02d} INFO worker-{i} processed job {i} in {i % 7}ms\n"
        for i in range(lines)
    )


def test_unwrap_rejects_things_that_are_not_envelopes():
    assert _terminal_envelope("plain text output") is None
    assert _terminal_envelope('{"output": "tiny", "exit_code": 0}') is None  # < 512 chars
    assert _terminal_envelope('{"just": "json", "no_output": 1}') is None
    assert _terminal_envelope('{"output": "x", "exit_code": 0}') is None


def test_unwrap_returns_the_inner_text():
    payload = json.dumps({"output": _log(20), "exit_code": 0, "error": None})
    inner, envelope = _terminal_envelope(payload)

    assert inner == _log(20)
    assert envelope["exit_code"] == 0


def test_hook_compresses_the_inner_text_and_keeps_the_envelope_valid():
    plugin._on_tool_result._ctx = _ConfigValues({})
    payload = json.dumps({"output": _log(), "exit_code": 0, "error": None})

    out = plugin._on_tool_result(
        tool_name="terminal", result=payload, args={"command": "run-worker"}, session_id="env-test"
    )

    assert out is not None
    parsed = json.loads(out)
    assert parsed["exit_code"] == 0
    assert len(parsed["output"]) < len(_log())


def test_emitted_ref_resolves_to_the_original_envelope():
    plugin._on_tool_result._ctx = _ConfigValues({})
    payload = json.dumps({"output": _log(), "exit_code": 0, "error": None})

    out = plugin._on_tool_result(
        tool_name="terminal", result=payload, args={"command": "run-worker"}, session_id="env-ref"
    )

    assert out is not None
    refs = re.findall(r"#(c[0-9A-Za-z]+)", out)
    assert refs, "no retrieval ref emitted"
    assert any(ccr.get("#" + ref) == payload for ref in refs), "ref does not resolve to the original"
