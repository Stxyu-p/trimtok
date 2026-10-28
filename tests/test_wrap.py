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


if __name__ == "__main__":
    unittest.main()
