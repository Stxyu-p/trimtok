"""compress_agent_payload is the documented dict-shaped entry point (README).

It is the one public API with no callers inside the package, so these tests are
the only thing pinning its contract.
"""
import unittest
from unittest import mock

import trimtok


def _big_log() -> str:
    return "".join("2026-11-13 10:00:%02d INFO worker-%d processed batch %d ok\n" % (i % 60, i % 4, i)
                   for i in range(400))


class AgentPayloadTests(unittest.TestCase):
    def test_terminal_mode_compresses_a_string(self):
        raw = _big_log()
        out, stats = trimtok.compress_agent_payload(raw, mode="terminal", command="ls -la")
        self.assertIsInstance(out, str)
        self.assertLess(len(out), len(raw))
        self.assertEqual(stats.get("gate"), "ok")

    def test_tool_mode_compresses_a_string(self):
        raw = _big_log()
        out, stats = trimtok.compress_agent_payload(raw, tool_name="terminal")
        self.assertLess(len(out), len(raw))
        self.assertIn("gate", stats)

    def test_dict_payload_replaces_only_the_content_key(self):
        raw = _big_log()
        payload = {"tool_name": "terminal", "content": raw, "id": "call-1"}
        out, stats = trimtok.compress_agent_payload(payload)
        self.assertIsInstance(out, dict)
        assert isinstance(out, dict)  # narrow for the type checker
        self.assertEqual(out["id"], "call-1")
        self.assertEqual(out["tool_name"], "terminal")
        self.assertLess(len(out["content"]), len(raw))
        self.assertIn("gate", stats)

    def test_dict_without_a_compressible_key_is_returned_untouched(self):
        payload = {"id": "call-2", "count": 3}
        out, stats = trimtok.compress_agent_payload(payload)
        self.assertEqual(stats.get("gate"), "passthrough_non_str")
        self.assertEqual(out, payload)

    def test_non_string_non_dict_is_returned_untouched(self):
        out, stats = trimtok.compress_agent_payload(42)  # type: ignore[arg-type]
        self.assertEqual(out, 42)
        self.assertEqual(stats.get("gate"), "passthrough_non_str")

    def test_a_failing_compressor_never_raises_out_of_the_api(self):
        with mock.patch("trimtok.compress_tool_result", side_effect=RuntimeError("boom")):
            out, stats = trimtok.compress_agent_payload("x" * 5000)
        self.assertEqual(out, "x" * 5000)
        self.assertEqual(stats.get("gate"), "passthrough_exception")


if __name__ == "__main__":
    unittest.main()
