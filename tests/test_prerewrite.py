import unittest
from trimtok.prerewrite import rewrite_command

class TestPreExecutionRewrite(unittest.TestCase):
    def test_pytest_flags_injected_when_absent(self):
        self.assertEqual(rewrite_command("pytest"), "pytest -q --tb=short")
        self.assertEqual(rewrite_command("pytest tests/"), "pytest tests/ -q --tb=short")

    def test_pytest_flags_skipped_when_explicit(self):
        self.assertEqual(rewrite_command("pytest -v"), "pytest -v")
        self.assertEqual(rewrite_command("pytest --tb=long"), "pytest --tb=long")
        self.assertEqual(rewrite_command("pytest -q"), "pytest -q")

    def test_git_log_flags_injected(self):
        self.assertEqual(rewrite_command("git log"), "git log --oneline -n 25")
        self.assertEqual(rewrite_command("git log -n 5"), "git log -n 5")

    def test_cargo_test_flags_injected(self):
        self.assertEqual(rewrite_command("cargo test"), "cargo test -- --quiet")
        self.assertEqual(rewrite_command("cargo test -- --nocapture"), "cargo test -- --nocapture")

    def test_piped_command_skipped(self):
        # Pipes indicate downstream parsers expecting exact formats
        self.assertEqual(rewrite_command("pytest | grep error"), "pytest | grep error")

    def test_unknown_command_passthrough(self):
        self.assertEqual(rewrite_command("ls -la"), "ls -la")

if __name__ == "__main__":
    unittest.main()
