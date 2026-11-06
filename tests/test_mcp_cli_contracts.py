"""Contract tests for the MCP / hook / CLI surface.

Each test pins a defect that let the tool lie to the caller: a destructive purge,
a false success, a marker that resolves to nothing, a bypassed policy, a failure
reported as success, and telemetry the summaries could not read.
"""
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="trimtok-mcp-"))

import trimtok as plugin
import trimtok.mcp_server as mcp
from trimtok import __main__ as cli


class _ConfigValues:
    def __init__(self, values=None):
        self.values = {**(values or {}), "usage_enabled": False}

    def get_config(self, key, default):
        return self.values.get(key, default)


def _success_looking_log() -> str:
    return "added 12 packages in 3s\n" + "\n".join(f"progress step {i} completed" for i in range(20))


def test_purge_rejects_a_negative_bound_instead_of_wiping_the_store():
    ns = SimpleNamespace(older_than=-1.0, max_mb=0.0)

    assert cli._run_purge(ns) == 2


def test_nonzero_exit_never_collapses_into_a_success_line():
    plugin._on_transform._ctx = _ConfigValues()

    out = plugin._on_transform(command="npm install", output=_success_looking_log(), returncode=1)

    assert out is None or "tt:success" not in out


def test_zero_exit_still_collapses():
    plugin._on_transform._ctx = _ConfigValues()

    out = plugin._on_transform(command="npm install", output=_success_looking_log(), returncode=0)

    assert out is not None and "tt:success" in out


def test_terminal_envelope_with_nonzero_exit_is_not_a_success():
    plugin._on_tool_result._ctx = _ConfigValues()
    payload = json.dumps({"output": _success_looking_log(), "exit_code": 1})

    out = plugin._on_tool_result(tool_name="terminal", result=payload, args={"command": "npm install"})

    assert out is None or "tt:success" not in out


def test_failed_ccr_write_does_not_emit_a_marker(monkeypatch):
    monkeypatch.setattr(mcp.ccr, "put", lambda *_a, **_k: "")
    log = "".join(f"2026-11-06 10:00:{i % 60:02d} INFO worker-{i} job {i}\n" for i in range(300))

    resp = mcp._tool_compress_terminal({"output": log, "command": "run"})

    assert resp["ref"] == ""
    assert "#" not in resp["compressed"].split("\n")[0] or "tt:" not in resp["compressed"]
    assert len(resp["compressed"]) == len(log)


def test_mcp_file_path_keeps_the_lockfile_lane(tmp_path: Path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {"name": "x", "lockfileVersion": 3,
             "packages": {f"node_modules/p{i}": {"version": f"1.0.{i}", "resolved": "https://r/" + "a" * 40}
                          for i in range(200)}},
            indent=2,
        ),
        encoding="utf-8",
    )

    resp = mcp._tool_compress_tool({"file_path": str(lock)})

    assert resp["stats"].get("kind") == "lockfile"
    assert len(resp["compressed"]) < lock.stat().st_size // 10


def test_mcp_file_path_still_honours_the_security_path_policy(tmp_path: Path):
    sec = tmp_path / "security" / "policy.yaml"
    sec.parent.mkdir()
    sec.write_text("allow:\n  - user:*\n" + "".join(f"rule_{i}: allow\n" for i in range(400)), encoding="utf-8")

    resp = mcp._tool_compress_tool({"file_path": str(sec)})

    assert resp["stats"].get("gate") == "passthrough_policy"
    assert resp["stats"].get("reason") == "security_path"


def test_cli_retrieve_reports_failure_through_the_exit_code():
    assert cli.main(["retrieve", "#nope"]) == 1


def test_mcp_retrieve_failure_sets_is_error():
    resp = mcp.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "trimtok_retrieve", "arguments": {"hash": "#nope"}},
    })

    assert resp["isError"] is True


def test_mcp_usage_rows_use_the_schema_the_summaries_read():
    row = mcp._usage_row("terminal", "run", {"before_chars": 100, "after_chars": 40,
                                             "saved_chars": 60, "kind": "log", "gate": "ok"})

    assert (row["before"], row["after"], row["saved"]) == (100, 40, 60)
    assert row["surface"] == "mcp"
    assert row["shape"] == "log"
