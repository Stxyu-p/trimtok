import unittest
from trimtok.signals import check_command_thrash, reset_thrash_tracker

class TestThrashGuard(unittest.TestCase):
    def setUp(self):
        reset_thrash_tracker()

    def test_detects_identical_failing_command_streak(self):
        for _ in range(2):
            self.assertIsNone(check_command_thrash("s1", "pytest", "FAILED test_a", 1))
        warning = check_command_thrash("s1", "pytest", "FAILED test_a", 1)
        self.assertIsNotNone(warning)
        self.assertIn("[tt:loop_warning streak=3", warning)

    def test_resets_streak_on_success(self):
        check_command_thrash("s1", "pytest", "FAILED", 1)
        check_command_thrash("s1", "pytest", "FAILED", 1)
        check_command_thrash("s1", "pytest", "PASSED", 0)
        self.assertIsNone(check_command_thrash("s1", "pytest", "FAILED", 1))

    def test_different_command_resets_streak(self):
        check_command_thrash("s1", "pytest", "FAILED", 1)
        check_command_thrash("s1", "pytest", "FAILED", 1)
        check_command_thrash("s1", "git status", "clean", 0)
        self.assertIsNone(check_command_thrash("s1", "pytest", "FAILED", 1))

if __name__ == "__main__":
    unittest.main()
