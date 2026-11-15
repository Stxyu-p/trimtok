"""Tests for Wave 1 & Wave 2 smart compression + lossless token savings."""
from __future__ import annotations

import json
import unittest

from trimtok.ccr import get as ccr_get
from trimtok.core import compress_terminal
from trimtok.dedup import clear_session, compress_tool_result
from trimtok.json_envelope import fold_envelope_array
from trimtok.pruners import fold_text_structure, prune_test_runner
from trimtok.signals import is_important


class TestSmartCompression(unittest.TestCase):
    def setUp(self):
        clear_session("test_smart_wave")

    def test_mid_band_code_keeps_helpers_up_to_15_lines(self):
        # 5KB Python file with 11-line helpers must NOT be folded under normal pressure.
        helper = (
            "def helper_{idx}(x):\n"
            + "".join(f"    v{k} = x + {k}\n" for k in range(1, 10))
            + "    return v9\n\n"
        )
        code = "".join(helper.format(idx=i) for i in range(28))
        self.assertTrue(4096 <= len(code) < 12000)
        numbered = "\n".join(f"{i+1}|{ln}" for i, ln in enumerate(code.splitlines()))
        out, stats = compress_tool_result(
            "read_file",
            numbered,
            args={"path": "src/mid_helpers.py"},
            session_id="test_smart_wave",
            min_chars=1000,
        )
        self.assertNotIn("[tt:body", out)
        self.assertIn("v9 = x + 9", out)

    def test_fold_text_structure_preserves_fenced_code_blocks(self):
        md = (
            "# Guide\n\n"
            + ("Long narrative paragraph line explaining context in detail.\n" * 12)
            + "\n```bash\n"
            + "\n".join(f"python run_step_{i}.py --flag {i}" for i in range(10))
            + "\n```\n\n"
            + ("Another long narrative paragraph line explaining more context.\n" * 12)
        )
        out, saved = fold_text_structure(md, ref="#abc123")
        self.assertGreater(saved, 200)
        self.assertIn("[tt:prose", out)
        for i in range(10):
            self.assertIn(f"python run_step_{i}.py --flag {i}", out)

    def test_web_extract_json_uses_web_shaver_not_terminal_window(self):
        # Build clean multi-section markdown article
        sections = "".join(
            f"## Section {n}\n" + (f"Detailed API explanation sentence {n}.\n" * 10) + "\n"
            for n in range(1, 6)
        )
        raw_md = (
            "# Docs Title\n"
            "Cookie Notice: Accept all cookies\n"
            "Navigation: Home > Docs > API\n\n"
            + sections
            + "Share on Twitter | LinkedIn\n"
        )
        payload = json.dumps({"results": [{"url": "https://example.com/docs", "title": "Docs", "content": raw_md}]})
        out, stats = compress_tool_result("web_extract", payload, session_id="test_smart_wave", min_chars=500)
        self.assertEqual(stats.get("gate"), "ok")
        self.assertEqual(stats.get("kind"), "web_intake")
        self.assertNotIn("[tt:terminal", out)
        self.assertNotIn("read_file(offset=", out)
        self.assertNotIn("Cookie Notice", out)
        self.assertIn("## Section 5", out)
        # Outer ref resolves in CCR
        import re
        refs = re.findall(r"#([0-9A-Za-z]+)", out.splitlines()[0])
        self.assertTrue(refs and ccr_get(refs[0]))

    def test_fold_envelope_array_omits_nested_target_path(self):
        payload = json.dumps({
            "success": True,
            "data": {
                "web": [
                    {"url": f"https://ex.com/{i}", "title": f"Result {i}", "description": f"Snippet text for item {i}", "position": i}
                    for i in range(1, 8)
                ]
            },
        })
        res = fold_envelope_array(payload)
        assert res is not None
        cand, meta = res
        self.assertEqual(meta["envelope_path"], "data.web")
        self.assertNotIn('"web":[', cand)

    def test_skills_list_repeat_cache_hit(self):
        payload = json.dumps({
            "success": True,
            "skills": [{"name": f"skill-{i}", "description": f"Does useful task {i}"} for i in range(30)],
        })
        out1, st1 = compress_tool_result("skills_list", payload, args={}, session_id="test_smart_wave", min_chars=500)
        out2, st2 = compress_tool_result("skills_list", payload, args={}, session_id="test_smart_wave", min_chars=500)
        self.assertEqual(st2.get("gate"), "ok")
        self.assertEqual(st2.get("kind"), "skills_list_cache_hit")
        self.assertTrue(out2.startswith("[tt:skills_list_cached"))
        self.assertLess(len(out2), 200)

    def test_search_files_common_prefix_with_mixed_windows_msys_paths(self):
        files = [f"C:/Users/gugmn/Workspace/trimtok/sub/file_{i}.py" for i in range(12)] + [
            "/c/Users/gugmn/Workspace/trimtok/sub/alt.py"
        ]
        payload = json.dumps({"total_count": len(files), "files": files})
        out, st = compress_tool_result("search_files", payload, session_id="test_smart_wave", min_chars=500)
        self.assertEqual(st.get("gate"), "ok")
        self.assertEqual(st.get("kind"), "search_files_prefix")
        parsed = json.loads(out)
        self.assertEqual(parsed["@prefix"], "C:/Users/gugmn/Workspace/trimtok/sub/")
        self.assertIn("file_0.py", parsed["files"])
        self.assertIn("/c/Users/gugmn/Workspace/trimtok/sub/alt.py", parsed["files"])

    def test_session_search_folds_long_messages_and_tool_calls_preserving_metadata(self):
        payload = json.dumps({
            "success": True,
            "results": [{
                "session_id": "sess-123",
                "title": "Fix trimtok",
                "link": "hermes://session/sess-123",
                "snippet": "matched snippet stays intact",
                "bookend_start": "A" * 1200,
                "messages": [
                    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": "X" * 1500}}]},
                    {"role": "assistant", "content": "B" * 1400},
                ],
            }],
        })
        out, st = compress_tool_result("session_search", payload, session_id="test_smart_wave", min_chars=500)
        self.assertEqual(st.get("gate"), "ok")
        self.assertEqual(st.get("kind"), "session_search_folded")
        self.assertIn("matched snippet stays intact", out)
        self.assertIn("hermes://session/sess-123", out)
        self.assertIn("read_file", out)
        self.assertLess(len(out), len(payload) // 2)

    def test_prune_test_runner_collapses_passes_keeps_failures(self):
        lines = [f"test_case_{i} (tests.test_suite.MyTest) ... ok" for i in range(25)]
        lines.insert(10, "test_broken_case (tests.test_suite.MyTest) ... FAIL")
        lines.extend([
            "======================================================================",
            "FAIL: test_broken_case (tests.test_suite.MyTest)",
            "AssertionError: expected 1 got 2",
            "Ran 26 tests in 1.2s",
            "FAILED (failures=1)",
        ])
        raw = "\n".join(lines)
        pruned, hidden = prune_test_runner(raw)
        self.assertEqual(hidden, 25)
        self.assertIn("[tt:tests", pruned)
        self.assertIn("FAIL: test_broken_case", pruned)
        self.assertIn("AssertionError: expected 1 got 2", pruned)
        out, st = compress_terminal("python -m unittest -v", raw, session_id="test_smart_wave")
        self.assertEqual(st.get("gate"), "ok")
        self.assertIn("FAIL: test_broken_case", out)

    def test_pass_verdict_lines_are_not_faults(self):
        # A test whose *name* mentions a fault is still a pass, and folding it is
        # safe. Misclassifying it made the delta lane announce "STILL FAILING" for
        # a green run, so the agent reported failures that never happened.
        for line in (
            "PASS log-keeps-error",
            "PASS ccr-write-failure-cleans-temp",
            "test_all_requirements_satisfied (tests.t.MyTest) ... ok",
            "ok  github.com/x/y  0.123s",
            "OK",
        ):
            self.assertFalse(is_important(line), line)
        for line in (
            "PASS write ... FAILED to flush",
            "FAIL: test_broken_case (tests.t.MyTest)",
            "ERROR: test_y (tests.t.MyTest)",
            "AssertionError: expected 1 got 2",
            "ok, but error: disk full",
        ):
            self.assertTrue(is_important(line), line)

    def test_green_run_delta_reports_zero_still_failing(self):
        raw = "\n".join(
            [f"test_long_descriptive_case_{i:02d} (tests.test_suite.Deeply.Nested.Case) ... ok" for i in range(40)]
            + ["Ran 40 tests in 0.20s", "", "OK"]
        )
        cmd = "python -m unittest -v"
        clear_session("delta_green")
        compress_terminal(cmd, raw, session_id="delta_green", delta_enabled=True)
        out, st = compress_terminal(cmd, raw, session_id="delta_green", delta_enabled=True)
        self.assertTrue(st.get("delta"), st)
        self.assertIn("!#0", out)
        self.assertNotIn("STILL FAILING", out)

    def test_new_failure_surfaces_in_delta_added_section(self):
        green = "\n".join(
            [f"test_long_descriptive_case_{i:02d} (tests.test_suite.Deeply.Nested.Case) ... ok" for i in range(40)]
            + ["Ran 40 tests in 0.20s", "", "OK"]
        )
        broken = green.replace(
            "test_long_descriptive_case_03 (tests.test_suite.Deeply.Nested.Case) ... ok",
            "test_long_descriptive_case_03 (tests.test_suite.Deeply.Nested.Case) ... FAIL",
        )
        cmd = "python -m unittest -v"
        clear_session("delta_broken")
        compress_terminal(cmd, green, session_id="delta_broken", delta_enabled=True)
        out, st = compress_terminal(cmd, broken, session_id="delta_broken", delta_enabled=True)
        self.assertTrue(st.get("delta"), st)
        self.assertIn("ADDED", out)
        self.assertIn("test_long_descriptive_case_03 (tests.test_suite.Deeply.Nested.Case) ... FAIL", out)


if __name__ == "__main__":
    unittest.main()
