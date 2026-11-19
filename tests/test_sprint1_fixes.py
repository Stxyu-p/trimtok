"""Sprint 1 regression & verification tests:
1. Terminal double-hook deduplication (_on_transform -> _on_tool_result with tool_call_id)
2. Hotspot extraction preserved on _on_transform even when _on_tool_result short-circuits
3. Delta forwarding & non-fallthrough in compress_tool_result("terminal", ...)
4. Single-# ref formatting across cmd_filter, timeout_bounded, skill_cache, skills_list_cache
5. Telemetry logging & reporting for trimtok_exec
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-sprint1-"))

import trimtok
from trimtok import ccr, core, lanes
from trimtok.dedup import compress_tool_result


class _Ctx:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    def get_config(self, key: str, default):
        return self.cfg.get(key, default)


class TestSprint1Fixes(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.usage_path = Path(self.temp.name) / "usage.jsonl"
        self.audit_path = Path(self.temp.name) / "audit.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _usage_rows(self) -> list[dict]:
        if not self.usage_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.usage_path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("{")
        ]

    def test_terminal_double_hook_deduplicated_and_hotspots_kept(self) -> None:
        ctx = _Ctx({"min_chars": 256})
        trimtok._on_transform._ctx = ctx
        trimtok._on_tool_result._ctx = ctx

        tb = (
            'Traceback (most recent call last):\n'
            '  File "pkg/worker.py", line 95, in run_job\n'
            '    raise RuntimeError("boom")\n'
            'RuntimeError: boom\n'
        )
        raw_output = ("2026-11-19 10:00:01 INFO tick\n" * 60) + tb
        call_id = "call_term_dedup_1"
        sid = "sess_double_hook"

        with patch.object(trimtok, "_usage_path", return_value=self.usage_path), \
             patch.object(trimtok, "_audit_path", return_value=self.audit_path):
            transformed = trimtok._on_transform(
                command="pytest tests/",
                output=raw_output,
                returncode=1,
                session_id=sid,
                tool_call_id=call_id,
            )
            self.assertIsNotNone(transformed)
            assert isinstance(transformed, str)

            # Hermes wraps the transformed output in JSON and fires transform_tool_result
            envelope = json.dumps({"output": transformed, "exit_code": 1})
            second_pass = trimtok._on_tool_result(
                tool_name="terminal",
                args={"command": "pytest tests/"},
                result=envelope,
                session_id=sid,
                tool_call_id=call_id,
            )
            self.assertIsNone(second_pass)

        # Only ONE telemetry row should be written (from _on_transform, not duplicated)
        rows = self._usage_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hook"], "terminal")

        # Causal hotspot captured during _on_transform must still protect read_file
        code_lines = ["def unrelated():"] + [
            f"    # long descriptive helper comment {i}\n    x_{i} = {i} * 100"
            for i in range(35)
        ] + [
            "    return 0",
            "",
            "def run_job():",
        ] + [
            f"    # long descriptive target comment {i}\n    y_{i} = {i} * 10"
            for i in range(35)
        ] + ["    return 1"]
        flat_lines = [sub for ln in code_lines for sub in ln.split("\n")]
        numbered = "\n".join(f"{i+1}|{ln}" for i, ln in enumerate(flat_lines))
        self.assertGreater(len(numbered), 2048)
        with patch.object(trimtok, "_usage_path", return_value=self.usage_path), \
             patch.object(trimtok, "_audit_path", return_value=self.audit_path):
            sk_out = trimtok._on_tool_result(
                tool_name="read_file",
                args={"path": "pkg/worker.py", "offset": 1, "limit": 2000},
                result=numbered,
                session_id=sid,
                tool_call_id="call_read_1",
            )
        self.assertIsNotNone(sk_out)
        assert isinstance(sk_out, str)
        self.assertIn("y_5 = 5 * 10", sk_out)
        self.assertIn("[tt:body L3-L71", sk_out)

    def test_terminal_envelope_forwards_delta_and_avoids_json_fallthrough(self) -> None:
        sid = "sess_env_delta"
        run1 = "\n".join(
            [f"test_suite.py::test_case_{i} PASSED" for i in range(60)]
            + ["FAILED test_suite.py::test_auth - AssertionError: 401 != 200"]
        )
        run2 = "\n".join(
            [f"test_suite.py::test_case_{i} PASSED" for i in range(60)]
            + ["FAILED test_suite.py::test_auth - AssertionError: 401 != 200"]
        )
        env1 = json.dumps({"output": run1, "exit_code": 1})
        env2 = json.dumps({"output": run2, "exit_code": 1})

        out1, st1 = compress_tool_result(
            "terminal", env1, args={"command": "pytest"}, session_id=sid,
            min_chars=256, delta_enabled=True,
        )
        self.assertEqual(st1["gate"], "ok")

        out2, st2 = compress_tool_result(
            "terminal", env2, args={"command": "pytest"}, session_id=sid,
            min_chars=256, delta_enabled=True,
        )
        self.assertEqual(st2["gate"], "ok")
        self.assertTrue(st2.get("delta"))
        parsed2 = json.loads(out2)
        self.assertIn("[tt:delta", parsed2["output"])

        # An incompressible envelope must not fall through into minify_json/volatile
        words = [
            "alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
            "golf", "hotel", "india", "juliet", "kilo", "lima",
            "mike", "november", "oscar", "papa", "quebec", "romeo",
        ]
        unique_lines = "\n".join(
            f"{words[i]}: distinct_payload_{words[-i - 1]}_value" for i in range(len(words))
        )
        env_unique = json.dumps({"output": unique_lines, "exit_code": 0})
        out_u, st_u = compress_tool_result(
            "terminal", env_unique, args={"command": "echo rows"}, session_id=sid,
            min_chars=256, delta_enabled=False, max_lines=100,
        )
        self.assertEqual(out_u, env_unique)
        self.assertEqual(st_u["saved_chars"], 0)
        self.assertNotEqual(st_u.get("kind"), "tool_volatile")

    def test_single_hash_ref_markers(self) -> None:
        # 1. cmd_filter
        git_log = "\n".join(
            f"commit {i:07x}\nAuthor: Alice <a@b.c>\nDate: Mon Nov 19 10:00:0{i} 2026\n\n    msg {i}\n"
            for i in range(6)
        )
        out_cmd, st_cmd = core.compress_terminal("git log", git_log, min_chars=64)
        self.assertEqual(st_cmd["gate"], "ok")
        self.assertNotIn("##c", out_cmd)
        self.assertRegex(out_cmd, r"#c[0-9A-Za-z]+")

        # 2. skill_cache & skills_list_cache
        skill_body = "# My Skill\n" + ("Detailed rule line.\n" * 40)
        lanes.skill_cache("skill_view", {"name": "demo-skill"}, skill_body, "s_ref", 4096, 0.0)
        hit1 = lanes.skill_cache("skill_view", {"name": "demo-skill"}, skill_body, "s_ref", 4096, 0.0)
        self.assertIsNotNone(hit1)
        assert hit1 is not None
        self.assertNotIn("##c", hit1[0])
        self.assertRegex(hit1[0], r"#c[0-9A-Za-z]+")

        catalogue = json.dumps({"skills": [{"name": f"s{i}", "desc": "x" * 40} for i in range(20)]})
        lanes.skills_list_cache("skills_list", {}, catalogue, "s_ref", 0.0)
        hit2 = lanes.skills_list_cache("skills_list", {}, catalogue, "s_ref", 0.0)
        self.assertIsNotNone(hit2)
        assert hit2 is not None
        self.assertNotIn("##c", hit2[0])
        self.assertRegex(hit2[0], r"#c[0-9A-Za-z]+")

    def test_exec_telemetry_logged_and_reported(self) -> None:
        ref = ccr.put(json.dumps([1, 2, 3, 4, 5]))
        trimtok._handle_exec._ctx = _Ctx()
        with patch.object(trimtok, "_usage_path", return_value=self.usage_path):
            raw_res = trimtok._handle_exec({
                "hash": ref,
                "code": "import json, os; print(sum(json.load(open(os.environ['TRIMTOK_PAYLOAD']))))",
            })
            parsed = json.loads(raw_res)
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["output"], "15")
            gain_txt = trimtok._gain_summary()

        rows = self._usage_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hook"], "exec")
        self.assertTrue(rows[0]["ok"])
        self.assertIn("exec: 1/1 ok", gain_txt)


if __name__ == "__main__":
    unittest.main()
