import unittest
from trimtok.dedup import compress_tool_result, clear_session
from trimtok.core import compress_terminal

class TestOptimizationsGroup1And2(unittest.TestCase):
    def setUp(self):
        clear_session("test_sess_1_2")

    def test_non_code_read_file_folds_under_12k(self):
        # Markdown file over 2KB and >= 40 lines with prose runs should fold structure, not passthrough
        section_text = "\n".join(f"Paragraph line {k} explaining feature details." for k in range(8))
        md_content = "# Title\n\n" + f"## Section\n{section_text}\n" * 6
        numbered = "\n".join(f"{i+1}|{l}" for i, l in enumerate(md_content.splitlines()))
        out, stats = compress_tool_result(
            "read_file",
            numbered,
            args={"path": "docs/guide.md"},
            session_id="test_sess_1_2",
            min_chars=1000,
        )
        self.assertEqual(stats.get("gate"), "ok")
        self.assertEqual(stats.get("kind"), "file_structure")
        self.assertIn("[tt:prose", out)

    def test_code_read_file_under_12k_stays_verbatim(self):
        # Python code under 12KB must stay verbatim for patch safety
        py_content = "def add(a, b):\n    return a + b\n" * 50
        numbered = "\n".join(f"{i+1}|{l}" for i, l in enumerate(py_content.splitlines()))
        out, stats = compress_tool_result(
            "read_file",
            numbered,
            args={"path": "src/math.py"},
            session_id="test_sess_1_2",
            min_chars=1000,
        )
        self.assertEqual(stats.get("gate"), "passthrough_reference")
        self.assertEqual(stats.get("reason"), "below_skeleton_min")
        self.assertEqual(out, numbered)

    def test_terminal_success_collapse_wired(self):
        noisy_npm = "added 150 packages in 2s\n" + ("noisy npm log line\n" * 80)
        out, stats = compress_terminal("npm install", noisy_npm)
        self.assertEqual(stats.get("gate"), "ok")
        self.assertEqual(stats.get("kind"), "success_collapse")
        self.assertTrue(out.startswith("[tt:success"))

    def test_skill_view_repeat_cache_hit(self):
        skill_text = "# Skill Guide\n" + ("Important instructions line\n" * 100)
        out1, stats1 = compress_tool_result(
            "skill_view",
            skill_text,
            args={"name": "git-workflow"},
            session_id="test_sess_1_2",
            min_chars=500,
        )
        # Second read in same session with identical text should be a cache hit stub
        out2, stats2 = compress_tool_result(
            "skill_view",
            skill_text,
            args={"name": "git-workflow"},
            session_id="test_sess_1_2",
            min_chars=500,
        )
        self.assertEqual(stats2.get("gate"), "ok")
        self.assertEqual(stats2.get("kind"), "skill_cache_hit")
        self.assertTrue(out2.startswith("[tt:skill_cached"))

    def test_timeout_bounded_on_large_payload(self):
        from trimtok.signals import Deadline
        import time
        # Huge output with an expired deadline should return bounded head+tail, not raw 50KB
        huge = ("INFO line with some log data\n" * 1500)
        dead = Deadline(budget_ms=1.0)
        time.sleep(0.005) # Force deadline expiration
        out, stats = compress_terminal("tail -n 1500 log", huge, deadline=dead)
        self.assertEqual(stats.get("gate"), "ok")
        self.assertEqual(stats.get("kind"), "timeout_bounded")
        self.assertIn("[tt:timeout_bounded", out)
        self.assertLess(len(out), 5000)

if __name__ == "__main__":
    unittest.main()
