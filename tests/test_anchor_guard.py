import unittest
from trimtok.signals import score_anchor_preservation

class TestAnchorPreservationGuard(unittest.TestCase):
    def test_anchor_preservation_score_calculation(self):
        orig = "File src/main.py:42 in run\nAssertionError in test_login\nExtra noise line 1\nExtra line 2"
        cand = "File src/main.py:42 in run\nAssertionError in test_login"
        score = score_anchor_preservation(orig, cand)
        self.assertEqual(score, 1.0)

    def test_compression_reverts_when_anchors_destroyed(self):
        orig = "tests/test_api.py:100: FAILED test_auth - KeyError: 'token'\n" + ("info log line\n" * 50)
        cand = "All tests finished.\ninfo log line"
        score = score_anchor_preservation(orig, cand)
        self.assertLess(score, 0.5)

    def test_empty_anchors_returns_one(self):
        orig = "plain text without code anchors\nsecond plain line"
        cand = "plain text"
        score = score_anchor_preservation(orig, cand)
        self.assertEqual(score, 1.0)

if __name__ == "__main__":
    unittest.main()
