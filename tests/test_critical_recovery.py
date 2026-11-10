"""Unit tests for Critical-Line Recovery backstop in trimtok."""

import unittest
from trimtok.core import compress_terminal


class TestCriticalLineRecovery(unittest.TestCase):
    def test_recovers_omitted_fault_lines_under_budget(self):
        # 50 unique error lines with a budget of 40 causes window to drop ~10 lines
        errors = [f"fatal: remote error {i} repository not found" for i in range(50)]
        raw = "\n".join(errors) + "\n" + "filler line\n" * 50

        out, stats = compress_terminal("git status", raw, max_lines=40)

        self.assertEqual(stats["gate"], "ok")
        self.assertIn("error line(s) recovered", out)
        self.assertEqual(stats.get("recovered_errors"), 10)
        self.assertIsNone(stats.get("omitted_errors"))

    def test_recovery_caps_at_20(self):
        # 150 unique error lines causes window to drop ~112 lines, cap limits recovery to 20
        errors = [f"fatal: remote error {i} repository not found" for i in range(150)]
        raw = "\n".join(errors) + "\n" + "filler line\n" * 50

        out, stats = compress_terminal("git status", raw, max_lines=40)

        self.assertEqual(stats["gate"], "ok")
        self.assertIn("[tt: 20 error line(s) recovered]", out)
        self.assertIn("more in #", out)
        self.assertEqual(stats.get("recovered_errors"), 20)
        self.assertGreater(stats.get("omitted_errors", 0), 0)


if __name__ == "__main__":
    unittest.main()
