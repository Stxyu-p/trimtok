"""Tests for `trimtok wrap -- <command>` standalone execution."""
import io
import sys
import unittest
from unittest import mock

from trimtok.__main__ import main


class TestWrapCommand(unittest.TestCase):
    def test_wrap_no_args_exits_nonzero(self):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            code = main(["wrap"])
        self.assertNotEqual(code, 0)
        self.assertIn("no command provided", stderr.getvalue().lower())

    def test_wrap_executes_child_and_returns_exit_code(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = main(["wrap", "--", sys.executable, "-c", "print('hello from child'); exit(0)"])
        self.assertEqual(code, 0)
        self.assertIn("hello from child", stdout.getvalue())

    def test_wrap_preserves_nonzero_exit_code(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = main(["wrap", "--", sys.executable, "-c", "print('failing child'); exit(42)"])
        self.assertEqual(code, 42)
        self.assertIn("failing child", stdout.getvalue())

    def test_wrap_applies_prerewrite_on_pytest(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(stdout="ok", stderr="", returncode=0)
            main(["wrap", "--", "pytest"])
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("-q", args)
            self.assertIn("--tb=short", args)


if __name__ == "__main__":
    unittest.main()
