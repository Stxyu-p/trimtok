"""Optional Hermes runtime integration test. Set HERMES_AGENT_PATH to enable it."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def main() -> int:
    agent_path = os.environ.get("HERMES_AGENT_PATH", "").strip()
    if not agent_path:
        print("SKIP: set HERMES_AGENT_PATH to a Hermes Agent source directory")
        return 0

    agent_root = Path(agent_path).expanduser().resolve()
    if not agent_root.is_dir():
        print(f"FAIL: HERMES_AGENT_PATH is not a directory: {agent_root}")
        return 1

    sys.path.insert(0, str(agent_root))
    try:
        from hermes_cli.plugin_dev import _doctor_runtime
        from tools.registry import registry
    except Exception as exc:
        print(
            "FAIL: could not import Hermes runtime; run this test with the Python "
            f"environment used by Hermes ({type(exc).__name__}: {exc})"
        )
        return 1

    plugin_path = Path(
        os.environ.get("TRIMTOK_PLUGIN_PATH", str(Path(__file__).resolve().parents[1]))
    ).expanduser().resolve()
    if not plugin_path.is_dir():
        print(f"FAIL: TRIMTOK_PLUGIN_PATH is not a directory: {plugin_path}")
        return 1
    expected_hooks = {
        "transform_terminal_output",
        "transform_tool_result",
        "post_llm_call",
        "on_session_start",
        "on_session_end",
    }

    with _doctor_runtime(plugin_path) as host:
        hooks = host.manager._hooks
        assert expected_hooks.issubset(hooks)
        assert all(len(hooks[name]) == 1 for name in expected_hooks)
        assert "trimtok_retrieve" in host.registered_tools

        home = Path(os.environ["HERMES_HOME"]).resolve()
        ccr_dir = (home / "plugin-data" / "trimtok").resolve()
        retrieve_tools = [
            entry.handler
            for entry in registry._snapshot_entries()
            if entry.name == "trimtok_retrieve"
        ]
        assert len(retrieve_tools) == 1
        retrieve = retrieve_tools[0]

        terminal_source = "\n".join(
            f"{i:04d} INFO worker-{i % 7} completed task payload-{i:04d}"
            for i in range(700)
        )
        terminal_output = hooks["transform_terminal_output"][0](
            command="integration-test", output=terminal_source, returncode=0
        )
        assert isinstance(terminal_output, str)
        assert len(terminal_output) < len(terminal_source)
        terminal_match = re.search(r"retrieve:([0-9a-f]{12})", terminal_output)
        assert terminal_match
        terminal_restored = json.loads(retrieve({"hash": terminal_match.group(1)}))
        assert terminal_restored.get("original_content") == terminal_source

        session_id = "trimtok-runtime-integration"
        hooks["on_session_start"][0](session_id=session_id)
        baseline = "\n".join(f"def func_{i}(): return {i}" for i in range(40)) + "\n" + "x" * 2100
        tool_source = "\n".join(f"def func_{i}(): return {i}" for i in range(40)) + "\n" + "y" * 2100
        hooks["transform_tool_result"][0](
            tool_name="search_files", result=baseline, session_id=session_id, turn_id="1"
        )
        tool_output = hooks["transform_tool_result"][0](
            tool_name="search_files", result=tool_source, session_id=session_id, turn_id="2"
        )
        assert isinstance(tool_output, str)
        assert len(tool_output) < len(tool_source)
        tool_match = re.search(r"retrieve:([0-9a-f]{12})", tool_output)
        assert tool_match
        tool_restored = json.loads(retrieve({"hash": tool_match.group(1)}))
        assert tool_restored.get("original_content") == tool_source

        hooks["post_llm_call"][0](session_id=session_id, turn_id="2")
        hooks["on_session_end"][0](session_id=session_id)
        assert ccr_dir.is_relative_to(home)
        assert ccr_dir.is_dir()

        print(
            f"PASS: terminal hook compressed {len(terminal_source)} -> "
            f"{len(terminal_output)} chars and retrieval restored the original"
        )
        print(
            f"PASS: tool-result hook compressed {len(tool_source)} -> "
            f"{len(tool_output)} chars and retrieval restored the original"
        )
        print("PASS: session lifecycle and post_llm_call hooks ran without errors")
        print(f"PASS: CCR stayed inside temporary HERMES_HOME: {ccr_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
