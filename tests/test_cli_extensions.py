import subprocess
import sys
from trimtok import mcp_server

def test_mcp_server_instructions_present():
    assert hasattr(mcp_server, "SERVER_INSTRUCTIONS")
    assert "trimtok_retrieve" in mcp_server.SERVER_INSTRUCTIONS or "trimtok retrieve" in mcp_server.SERVER_INSTRUCTIONS

    # Verify initialize message returns instructions
    init_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"}
    }
    resp = mcp_server.handle(init_msg)
    assert resp is not None
    assert "instructions" in resp
    assert "trimtok" in resp["instructions"].lower()

def test_cli_map_subcommand():
    proc = subprocess.run(
        [sys.executable, "-m", "trimtok", "map", "--budget", "500"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Codebase map" in proc.stdout or proc.stdout.strip() == ""

def test_cli_mcp_proxy_subcommand_help():
    proc = subprocess.run(
        [sys.executable, "-m", "trimtok", "mcp-proxy", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--allow" in proc.stdout
    assert "--deny" in proc.stdout
    assert "--kv-lock" in proc.stdout

def test_cli_kv_lock_subcommand(tmp_path):
    f = tmp_path / "prompt.txt"
    f.write_text("System started on 2026-11-05.\nRules: be brief.", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "trimtok", "kv-lock", str(f)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "System started on <date>." in proc.stdout
    assert "<!-- kv-lock: dynamic context -->" in proc.stdout
    assert "2026-11-05" in proc.stdout

def test_hermes_pre_llm_call_hook():
    from trimtok import _pre_llm_call
    res = _pre_llm_call(session_id="test", conversation_history=[])
    # Might be None if no codebase or string context
    assert res is None or ("context" in res and "Codebase map" in res["context"])

    # If already in conversation, must be None
    res_cached = _pre_llm_call(
        session_id="test",
        conversation_history=[{"api_content": "Codebase map (2 directories):\nsrc/: app"}]
    )
    assert res_cached is None
