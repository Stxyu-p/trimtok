import unittest
from trimtok.pruners import collapse_success_output, group_directory_siblings

class TestPrunersNextGen(unittest.TestCase):
    def test_collapse_success_package_managers(self):
        noisy_npm = "added 124 packages in 2.1s\n" + ("...\n" * 100) + "audited 125 packages in 2s"
        res = collapse_success_output("npm install", noisy_npm, exit_code=0)
        self.assertTrue(res.startswith("[tt:success"))
        self.assertLess(len(res), 150)

    def test_collapse_success_skipped_on_error(self):
        failing = "npm ERR! code ERESOLVE\n" + ("...\n" * 50)
        res = collapse_success_output("npm install", failing, exit_code=1)
        self.assertEqual(res, failing)

    def test_collapse_success_skipped_when_error_in_text(self):
        tricky = "npm warn deprecated\nTraceback (most recent call last):\nError: something broke"
        res = collapse_success_output("npm install", tricky, exit_code=0)
        self.assertEqual(res, tricky)

    def test_group_directory_siblings_git_status(self):
        lines = [
            "M src/a/1.py",
            "M src/a/2.py",
            "M src/a/3.py",
            "M src/a/4.py",
            "M src/a/5.py",
            "M other/b.py",
        ]
        grouped = group_directory_siblings(lines, threshold=5)
        self.assertTrue(any("src/a/ (5 files modified) [tt:grouped]" in l for l in grouped))
        self.assertTrue(any("other/b.py" in l for l in grouped))
        self.assertEqual(len(grouped), 2)

    def test_group_directory_siblings_below_threshold_untouched(self):
        lines = [
            "M src/a/1.py",
            "M src/a/2.py",
            "M other/b.py",
        ]
        grouped = group_directory_siblings(lines, threshold=5)
        self.assertEqual(grouped, lines)

if __name__ == "__main__":
    unittest.main()
