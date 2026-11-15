"""Tests for TrimtokContextEngine: the Hermes ContextEngine implementation.

Runs under `unittest discover`, so the store is redirected before trimtok is
imported -- otherwise a stashing test would write test payloads into the real
~/.trimtok CCR and telemetry.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-ctxengine-"))

import trimtok  # noqa: E402
import trimtok.ccr as ccr  # noqa: E402
from trimtok.context_engine import TrimtokContextEngine  # noqa: E402

BULKY = "TEST LOG\n" * 300  # 2,700 chars, above the default stash floor


def _messages(bulky_content: str = BULKY) -> list[dict]:
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Run tests please."},
        {"role": "assistant", "content": "Running tests now..."},
        {"role": "tool", "tool_call_id": "call_1", "content": bulky_content},
        {"role": "assistant", "content": "Tests had some output. Next checking files..."},
        {"role": "tool", "tool_call_id": "call_2", "content": "row of data\n" * 300},
        {"role": "assistant", "content": "Now fixing the bug in code."},
        {"role": "user", "content": "Looks good, finish up."},
        {"role": "assistant", "content": "Done!"},
    ]


class TestContextEngineContract(unittest.TestCase):
    def test_contract(self):
        engine = TrimtokContextEngine(context_length=128000, threshold_percent=0.50)
        self.assertEqual(engine.name, "trimtok")
        self.assertEqual(engine.context_length, 128000)
        self.assertEqual(engine.threshold_percent, 0.50)
        self.assertEqual(engine.threshold_tokens, 64000)
        self.assertGreaterEqual(engine.protect_first_n, 3)
        self.assertGreaterEqual(engine.protect_last_n, 6)

        engine.update_from_response({"prompt_tokens": 70000, "completion_tokens": 1500, "total_tokens": 71500})
        self.assertEqual(engine.last_prompt_tokens, 70000)
        self.assertEqual(engine.last_completion_tokens, 1500)
        self.assertEqual(engine.last_total_tokens, 71500)

        self.assertFalse(engine.should_compress(prompt_tokens=50000))
        self.assertTrue(engine.should_compress(prompt_tokens=70000))


class TestContextEngineCompress(unittest.TestCase):
    def test_middle_stashing_protects_head_and_tail(self):
        engine = TrimtokContextEngine(protect_first_n=2, protect_last_n=2)
        messages = _messages()
        compressed = engine.compress(messages)

        self.assertEqual(len(compressed), len(messages))
        self.assertEqual(compressed[0]["content"], "You are a helpful assistant.")
        self.assertEqual(compressed[1]["content"], "Run tests please.")
        self.assertEqual(compressed[2]["content"], "Running tests now...")
        self.assertEqual(compressed[-2]["content"], "Looks good, finish up.")
        self.assertEqual(compressed[-1]["content"], "Done!")
        self.assertEqual(compressed[4]["content"], "Tests had some output. Next checking files...")
        self.assertEqual(compressed[6]["content"], "Now fixing the bug in code.")

        stub = compressed[3]["content"]
        self.assertIn("[tt:stashed", stub)
        self.assertIn("trimtok_retrieve", stub)
        match = re.search(r"(#c[0-9a-zA-Z]+)", stub)
        if match is None:
            self.fail(f"expected a ref marker in {stub!r}")
        self.assertEqual(ccr.get(match.group(1)), BULKY)

    def test_small_tool_result_is_not_stashed(self):
        # Below the floor the stub would cost more than the payload saves.
        engine = TrimtokContextEngine(protect_first_n=2, protect_last_n=2, min_stash_chars=2048)
        small = "short output\n" * 20  # 260 chars
        compressed = engine.compress(_messages(small))
        self.assertEqual(compressed[3]["content"], small)
        self.assertEqual(engine.compression_count, 1)  # only the second bulky result

    def test_fault_bearing_result_stays_verbatim(self):
        # The agent is debugging this traceback; stashing it hides the one line it needs.
        engine = TrimtokContextEngine(protect_first_n=2, protect_last_n=2)
        noisy = BULKY + "Traceback (most recent call last):\n  File \"x.py\", line 3, in f\nValueError: boom\n"
        compressed = engine.compress(_messages(noisy))
        self.assertEqual(compressed[3]["content"], noisy)
        self.assertNotIn("[tt:stashed", compressed[3]["content"])

    def test_dead_pointer_is_never_emitted(self):
        # If the store refuses the write, keep the original: a stub with an empty
        # ref is a pointer the agent can never resolve.
        engine = TrimtokContextEngine(protect_first_n=2, protect_last_n=2)
        with patch.object(ccr, "put", return_value=""):
            compressed = engine.compress(_messages())
        self.assertEqual(compressed[3]["content"], BULKY)
        self.assertNotIn("[tt:stashed", compressed[3]["content"])
        self.assertEqual(engine.compression_count, 0)

    def test_already_stashed_content_is_left_alone(self):
        engine = TrimtokContextEngine(protect_first_n=2, protect_last_n=2)
        stub = "[tt:stashed tool result (10 lines, 2,000 chars) · trimtok_retrieve '#cabc']" + "x" * 3000
        compressed = engine.compress(_messages(stub))
        self.assertEqual(compressed[3]["content"], stub)

    def test_no_stash_means_no_compression_count(self):
        engine = TrimtokContextEngine(protect_first_n=2, protect_last_n=2, min_stash_chars=10**9)
        engine.compress(_messages())
        self.assertEqual(engine.compression_count, 0)

    def test_empty_and_short_histories_are_returned_unchanged(self):
        engine = TrimtokContextEngine()
        self.assertEqual(engine.compress([]), [])
        tiny = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        out = engine.compress(tiny)
        self.assertEqual([m["content"] for m in out], ["s", "u"])


class TestContextEngineClone(unittest.TestCase):
    def test_clone_copies_settings_and_is_isolated(self):
        engine = TrimtokContextEngine(
            context_length=200000, threshold_percent=0.40, protect_first_n=4, protect_last_n=10,
            min_stash_chars=4096, fault_scan_lines=500,
        )
        engine.threshold_tokens = 80000
        engine.last_prompt_tokens = 50000

        cloned = engine.clone_for_agent()
        self.assertIsNot(cloned, engine)
        self.assertIsInstance(cloned, TrimtokContextEngine)
        self.assertEqual(cloned.context_length, 200000)
        self.assertEqual(cloned.threshold_percent, 0.40)
        self.assertEqual(cloned.threshold_tokens, 80000)
        self.assertEqual(cloned.protect_first_n, 4)
        self.assertEqual(cloned.protect_last_n, 10)
        self.assertEqual(cloned.min_stash_chars, 4096)
        self.assertEqual(cloned.fault_scan_lines, 500)

        cloned.threshold_tokens = 50000
        self.assertEqual(engine.threshold_tokens, 80000)


class TestContextEngineRegistration(unittest.TestCase):
    def test_plugin_register_context_engine(self):
        ctx = MagicMock()
        trimtok.register(ctx)
        self.assertTrue(ctx.register_context_engine.called)
        engine_arg = ctx.register_context_engine.call_args[0][0]
        self.assertIsInstance(engine_arg, TrimtokContextEngine)
        self.assertEqual(engine_arg.name, "trimtok")


if __name__ == "__main__":
    unittest.main()
