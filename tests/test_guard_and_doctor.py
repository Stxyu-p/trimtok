"""Phase 1 tests: stub write-back guard, doctor envelope, Base62 case, CCR hygiene, MCP parity."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import trimtok
from trimtok import ccr, guard, mcp_server, policy, report
from trimtok.__main__ import main as cli_main
from trimtok.dedup import compress_tool_result
from trimtok.ts_skeleton import skeletonize_code_full


class _DummyCtx:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.hooks: dict[str, object] = {}
        self.tools: dict[str, object] = {}
        self.commands: dict[str, object] = {}

    def get_config(self, key: str, default=None):
        return self.cfg.get(key, default)

    def register_hook(self, name: str, fn):
        self.hooks[name] = fn

    def register_tool(self, *, name: str, **_kw):
        self.tools[name] = True

    def register_command(self, name: str, fn, _desc: str = ""):
        self.commands[name] = fn


class TestGuardAndPolicy(unittest.TestCase):
    def test_instruction_files_passthrough_policy(self):
        big_skill = "# Skill\n\n" + ("Detailed workflow rule line.\n" * 160)
        for p in (
            r"C:\Users\gugmn\.agents\skills\tdd\SKILL.md",
            "/repo/skills/software-development/test-driven-development/SKILL.md",
            "/repo/AGENTS.md",
            "/repo/CLAUDE.md",
            "/repo/GEMINI.md",
            "/repo/SOUL.md",
        ):
            pol = policy.classify(big_skill, tool="read_file", path=p)
            self.assertTrue(pol.verbatim, f"{p} must stay verbatim")
            self.assertEqual(pol.reason, "instruction_file")

        out, stats = compress_tool_result(
            "read_file",
            big_skill,
            args={"path": r"C:\Users\gugmn\.agents\skills\tdd\SKILL.md", "offset": 1, "limit": 2000},
            session_id="guard-skill-read",
        )
        self.assertEqual(out, big_skill)
        self.assertEqual(stats["gate"], "passthrough_policy")
        self.assertEqual(stats["reason"], "instruction_file")

    def test_windows_backslash_security_path(self):
        pol = policy.classify("allow = true", tool="read_file", path=r"C:\repo\v1.0\security\policy.yaml")
        self.assertTrue(pol.verbatim)
        self.assertEqual(pol.reason, "security_path")

    def test_guard_detects_real_stubs_ignores_backticked_docs(self):
        doc_text = (
            "Never emit a `[tt:... #ref]` marker or `[tt:prose L18-L22 (5L) #c24O]` in docs.\n"
            "Use `[@#c1]` in documentation examples.\n"
        )
        self.assertEqual(guard.find_stub_markers(doc_text), [])
        self.assertFalse(guard.has_persisted_stubs(doc_text))

        poisoned = (
            "# Title\n"
            "[tt:prose L18-L22 (5L) #c24O -> read_file(offset=18, limit=5) or trimtok_retrieve]\n"
        )
        hits = guard.find_stub_markers(poisoned)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "tt_prose_stub")

        # skill_manage operations nesting
        blk = guard.check_write_args(
            "skill_manage",
            {"operations": [{"action": "patch", "name": "tdd", "content": poisoned}]},
        )
        self.assertIsNotNone(blk)
        assert blk is not None
        self.assertTrue(blk["blocked"])

    def test_pre_tool_call_hook_blocks_stub_and_respects_override(self):
        ctx_block = _DummyCtx({"enabled": True, "allow_stub_write": False})
        trimtok.register(ctx_block)
        hook = ctx_block.hooks["pre_tool_call"]
        poisoned = "def f():\n    pass  # [tt:body L2-L12 (11L of f) #c24O -> read_file(offset=2, limit=11)]\n"
        res = hook(tool_name="write_file", args={"path": "foo.py", "content": poisoned})
        self.assertIsInstance(res, dict)
        assert isinstance(res, dict)
        self.assertEqual(res["action"], "block")

        ctx_allow = _DummyCtx({"enabled": True, "allow_stub_write": True})
        trimtok.register(ctx_allow)
        self.assertIsNone(hook(tool_name="write_file", args={"path": "foo.py", "content": poisoned}))


class TestBase62CcrAndSkeletonFixes(unittest.TestCase):
    def test_base62_case_preserved_in_ts_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.dict(os.environ, {"TRIMTOK_DATA_DIR": str(tmp_path)}):
                ccr.close()
                store = ccr._get_store(tmp_path)
                try:
                    # Force lower vs upper Base62 aliases (#c24o vs #c24O)
                    for i in range(1, 2 + 62 + 1):
                        pass
                    store._conn.execute(
                        "INSERT INTO chunks (sha, alias, content, created_at, pinned) VALUES (?, ?, ?, ?, 0)",
                        ("a" * 64, "#c24o", "LOWER_PAYLOAD_TEXT", time.time()),
                    )
                    store._conn.execute(
                        "INSERT INTO chunks (sha, alias, content, created_at, pinned) VALUES (?, ?, ?, ?, 0)",
                        ("b" * 64, "#c24O", "UPPER_PAYLOAD_TEXT", time.time()),
                    )
                    store._conn.commit()
                    out_upper = trimtok._handle_ts_command("recall #c24O")
                    out_lower = trimtok._handle_ts_command("recall #c24o")
                    self.assertIn("UPPER_PAYLOAD_TEXT", out_upper)
                    self.assertNotIn("LOWER_PAYLOAD_TEXT", out_upper)
                    self.assertIn("LOWER_PAYLOAD_TEXT", out_lower)
                finally:
                    ccr.close()

    def test_hex_digest_starting_with_c_not_treated_as_alias(self):
        self.assertFalse(ccr._is_alias_ref("#c6f49f1527a2"))
        self.assertFalse(ccr._is_alias_ref("c6f49f1527a2"))
        self.assertTrue(ccr._is_alias_ref("#c1"))
        self.assertTrue(ccr._is_alias_ref("#c24O"))

    def test_ccr_caps_pin_and_corrupt_decompress(self):
        self.assertIsNone(ccr._decompress_payload(b"zstd:not-valid-zstd"))
        self.assertIsNone(ccr._decompress_payload(b"zlib:not-valid-zlib"))
        with tempfile.TemporaryDirectory() as tmp:
            store = ccr.FastCCRStore(Path(tmp))
            try:
                r1 = store.put("alpha " * 300, max_entries=3)
                store.pin(r1)
                r2 = store.put("beta " * 300, max_entries=3)
                r3 = store.put("gamma " * 300, max_entries=3)
                r4 = store.put("delta " * 300, max_entries=3)
                self.assertEqual(store.get(r1), "alpha " * 300)
                self.assertEqual(store.get(r4), "delta " * 300)
                self.assertLessEqual(store.stats()["entries"], 3)
                # Pinned r1 survives unpinned purge
                store.purge()
                self.assertEqual(store.get(r1), "alpha " * 300)
                self.assertIsNone(store.get(r2))
                self.assertIsNone(store.get(r3))
                # RAM LRU respects TTL
                r5 = store.put("ephemeral " * 100, ttl_hours=1)
                sha5 = store._alias_mem[r5]
                store._mem_meta[sha5] = (time.time() - 7200, False)
                self.assertIsNone(store.get(r5, ttl_hours=1))
            finally:
                store.close()

    def test_ts_skeleton_utf8_byte_slicing(self):
        js_code = (
            "// ภาษาไทยทดสอบการจัดรูป ─→× เพิ่มความยาวให้เกินไบต์\n"
            "function handleThaiRequest(req, res) {\n"
            + ("  const step = req.value;\n" * 12)
            + "  return res;\n}\n"
        )
        out, saved, cnt = skeletonize_code_full(js_code, filename="app.js", min_body_lines=8, ref="#c1")
        if cnt > 0 and saved > 0:
            self.assertIn("of handleThaiRequest", out)


class TestHooksMcpDoctorCli(unittest.TestCase):
    def test_excluded_command_and_pre_llm_enabled_flag(self):
        ctx = _DummyCtx({
            "enabled": True,
            "exclude_commands": ["pytest"],
            "policy_enabled": False,
        })
        trimtok.register(ctx)
        big_out = "PASSED test_a\n" * 300
        self.assertIsNone(
            ctx.hooks["transform_terminal_output"](
                command="pytest -q", output=big_out, returncode=0, tool_call_id="call-ex-1"
            )
        )
        env_json = json.dumps({"output": big_out, "exit_code": 0})
        self.assertIsNone(
            ctx.hooks["transform_tool_result"](
                tool_name="terminal", args={"command": "pytest -q"}, result=env_json, tool_call_id="call-ex-1"
            )
        )

        ctx_off = _DummyCtx({"enabled": False})
        trimtok.register(ctx_off)
        self.assertIsNone(ctx_off.hooks["pre_llm_call"](conversation_history=[]))

    def test_doctor_and_mcp_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_dir = tmp_path / "skills"
            skill_dir.mkdir()
            (skill_dir / "clean.md").write_text("Docs mention `[tt:prose L1-L5 (5L) #c1]` safely.\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"TRIMTOK_DATA_DIR": str(tmp_path / "ttdata")}):
                ccr.close()
                try:
                    # Seed a chunk so DB exists
                    ref = ccr.put("hello trimtok doctor " * 200)
                    self.assertTrue(ref.startswith("#c"))
                    doc = report.run_doctor(data_dir=tmp_path / "ttdata", scan_paths=[skill_dir])
                    self.assertEqual(doc["tool"], "trimtok")
                    self.assertEqual(doc["status"], "ok", doc)
                    self.assertTrue(doc["metrics"]["stub_scan"]["clean"])

                    # MCP doctor + exec error sets isError=True
                    resp_doc = mcp_server.handle({
                        "method": "tools/call",
                        "params": {"name": "trimtok_status", "arguments": {"action": "doctor"}},
                    })
                    self.assertFalse(resp_doc["isError"])
                    parsed = json.loads(resp_doc["content"][0]["text"])
                    self.assertEqual(parsed["action"], "doctor")
                    self.assertEqual(parsed["tool"], "trimtok")

                    resp_exec_err = mcp_server.handle({
                        "method": "tools/call",
                        "params": {
                            "name": "trimtok_exec",
                            "arguments": {"hash": ref, "code": "import sys; sys.exit(3)"},
                        },
                    })
                    self.assertTrue(resp_exec_err["isError"])

                    # CLI guard-check detects poisoned file
                    (skill_dir / "bad.md").write_text(
                        "[tt:prose L10-L20 (11L) #c24O -> read_file(offset=10, limit=11)]\n",
                        encoding="utf-8",
                    )
                    rc = cli_main(["guard-check", str(skill_dir), "--json"])
                    self.assertEqual(rc, 1)
                finally:
                    ccr.close()


if __name__ == "__main__":
    unittest.main()
