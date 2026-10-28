import unittest
from trimtok.compaction import stash_old_tool_results
from trimtok.ccr import get as ccr_get

class TestCompactionStash(unittest.TestCase):
    def test_stashing_replaces_old_tool_results_with_ccr_ref(self):
        messages = [
            {"role": "user", "content": "run tests"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "name": "terminal"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "1000 lines of test output " * 50},
            {"role": "assistant", "content": "I see the tests failed."},
            {"role": "user", "content": "fix it"},
            {"role": "assistant", "tool_calls": [{"id": "call_2", "name": "patch"}]},
            {"role": "tool", "tool_call_id": "call_2", "content": "patched file ok"},
            {"role": "assistant", "content": "done"},
        ]
        stashed = stash_old_tool_results(messages, preserve_recent_turns=1)
        old_tool = stashed[2]["content"]
        self.assertTrue(old_tool.startswith("[tt:stashed"))
        self.assertIn("trimtok_retrieve", old_tool)
        self.assertEqual(stashed[0]["content"], "run tests")
        self.assertEqual(stashed[6]["content"], "patched file ok")

    def test_short_tool_results_not_stashed(self):
        messages = [
            {"role": "tool", "tool_call_id": "call_0", "content": "short output"},
            {"role": "user", "content": "next"},
        ]
        stashed = stash_old_tool_results(messages, preserve_recent_turns=0, min_chars=100)
        self.assertEqual(stashed[0]["content"], "short output")

if __name__ == "__main__":
    unittest.main()
