import json
from trimtok.mcp_proxy import filter_mcp_message

def test_filter_mcp_tools_list_allow():
    sample_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "core_read", "description": "Read file"},
                {"name": "admin_purge", "description": "Purge cache"},
                {"name": "admin_doctor", "description": "Doctor check"}
            ]
        }
    }
    raw = json.dumps(sample_response).encode("utf-8")
    filtered = filter_mcp_message(raw, allow_tools={"core_read"})
    data = json.loads(filtered.decode("utf-8"))
    
    tools = [t["name"] for t in data["result"]["tools"]]
    assert tools == ["core_read"]
    assert "admin_purge" not in tools

def test_filter_mcp_tools_list_deny():
    sample_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "core_read", "description": "Read file"},
                {"name": "admin_purge", "description": "Purge cache"},
                {"name": "admin_doctor", "description": "Doctor check"}
            ]
        }
    }
    raw = json.dumps(sample_response).encode("utf-8")
    filtered = filter_mcp_message(raw, deny_tools={"admin_purge", "admin_doctor"})
    data = json.loads(filtered.decode("utf-8"))
    
    tools = [t["name"] for t in data["result"]["tools"]]
    assert tools == ["core_read"]

def test_filter_mcp_initialize_instructions_with_kv_lock():
    sample_init = {
        "jsonrpc": "2.0",
        "id": 0,
        "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "test", "version": "1.0"},
            "capabilities": {},
            "instructions": "Session started: 2026-11-05\nDo your task."
        }
    }
    raw = json.dumps(sample_init).encode("utf-8")
    filtered = filter_mcp_message(raw, kv_lock=True)
    data = json.loads(filtered.decode("utf-8"))
    assert "<date>" in data["result"]["instructions"]
    assert "<!-- kv-lock: dynamic context -->" in data["result"]["instructions"]
    assert "2026-11-05" in data["result"]["instructions"]


def test_filter_mcp_content_length_header():
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "tool_a"},
                {"name": "tool_b"}
            ]
        }
    })
    raw = f"Content-Length: {len(body)}\r\n\r\n{body}".encode("utf-8")
    filtered = filter_mcp_message(raw, allow_tools={"tool_a"})
    assert filtered.startswith(b"Content-Length: ")
    header, sep, rest = filtered.partition(b"\r\n\r\n")
    data = json.loads(rest.decode("utf-8"))
    assert len(data["result"]["tools"]) == 1
    assert data["result"]["tools"][0]["name"] == "tool_a"
    length = int(header.split(b": ")[1])
    assert length == len(rest)

def test_filter_mcp_fail_open_on_broken_json():
    broken = b"invalid json data {["
    assert filter_mcp_message(broken) == broken
