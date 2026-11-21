"""Tests for TrimtokContextEngine: the Hermes ContextEngine implementation.

Runs under `unittest discover`, so the store is redirected before trimtok is
imported -- otherwise a stashing test would write test payloads into the real
~/.trimtok CCR and telemetry.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import types
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


class TestContextEngineUpgradedLayers(unittest.TestCase):
    def test_tier0_proactive_prune_respects_turn_start_and_cold_cache(self):
        engine = TrimtokContextEngine(
            context_length=100_000, threshold_percent=0.50, protect_first_n=1, protect_last_n=1
        )
        msgs = _messages()
        # Below proactive trigger on warm cache -> no-op, exact same list identity
        out, n = engine.prune_tool_results_only(msgs, current_tokens=10_000, cold_cache=False)
        self.assertIs(out, msgs)
        self.assertEqual(n, 0)

        # Cold cache with turn_start=5 protects index >= 5 (only index 3 is pruned)
        out2, n2 = engine.prune_tool_results_only(msgs, current_tokens=10_000, cold_cache=True, turn_start=5)
        self.assertIsNot(out2, msgs)
        self.assertEqual(n2, 1)
        self.assertIn("[tt:stashed", out2[3]["content"])
        self.assertEqual(out2[5]["content"], msgs[5]["content"])

    def test_tier1_stashes_oversized_tool_call_args_and_user_paste(self):
        engine = TrimtokContextEngine(protect_first_n=1, protect_last_n=1)
        big_code = json.dumps({"path": "src/app.py", "content": "x = 1\n" * 600})
        big_paste = "user pasted log line\n" * 300
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "initial"},
            {"role": "user", "content": big_paste},
            {
                "role": "assistant",
                "content": "writing file",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": big_code}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]
        compressed = engine.compress(msgs)
        # Original input is not mutated
        self.assertEqual(msgs[3]["tool_calls"][0]["function"]["arguments"], big_code)

        # Tool call arguments replaced with valid JSON pointing to CCR ref
        new_args = json.loads(compressed[3]["tool_calls"][0]["function"]["arguments"])
        self.assertIn("tt_stashed_ref", new_args)
        self.assertEqual(ccr.get(new_args["tt_stashed_ref"]), big_code)

        # Oversized user paste stashed with resolvable CCR ref
        self.assertIn("[tt:stashed user payload", compressed[2]["content"])
        m = re.search(r"(#c[0-9a-zA-Z]+)", compressed[2]["content"])
        self.assertIsNotNone(m)
        self.assertEqual(ccr.get(m.group(1)), big_paste)

    def test_tier2_hybrid_llm_escalation_and_ccr_archive(self):
        engine = TrimtokContextEngine(
            context_length=10_000, threshold_percent=0.50, protect_first_n=1, protect_last_n=2,
            target_ratio=0.50, enable_llm_summary=True,
        )
        # Long user/assistant conversation with no bulky tools -> Tier 1 cannot shrink it
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "start"}]
        for idx in range(12):
            msgs.append({"role": "user", "content": f"Step {idx}: explain architecture detail " + ("word " * 120)})
            msgs.append({"role": "assistant", "content": f"Answer {idx}: decided to refactor module " + ("detail " * 120)})
        msgs.append({"role": "user", "content": "latest question"})
        msgs.append({"role": "assistant", "content": "latest reply"})

        fake_aux = types.ModuleType("agent.auxiliary_client")
        fake_aux.call_llm = MagicMock(return_value={"choices": [{"message": {"content": "## Decisions\n- Refactored modules 0..11 cleanly."}}]})
        fake_aux.extract_content_or_reasoning = lambda resp: resp["choices"][0]["message"]["content"]

        fake_agent_pkg = types.ModuleType("agent")
        with patch.dict(sys.modules, {"agent": fake_agent_pkg, "agent.auxiliary_client": fake_aux}):
            compressed = engine.compress(msgs, current_tokens=6000)

        self.assertTrue(fake_aux.call_llm.called)
        self.assertLess(len(compressed), len(msgs))
        handoff = compressed[2]
        self.assertTrue(handoff.get("_compressed_summary"))
        self.assertIn("hybrid_llm", handoff["content"])
        self.assertIn("Refactored modules 0..11", handoff["content"])
        m = re.search(r"trimtok_retrieve '(#c[0-9a-zA-Z]+)'", handoff["content"])
        self.assertIsNotNone(m)
        archived = ccr.get(m.group(1))
        self.assertIn("Step 0:", archived)
        self.assertIn("Answer 0:", archived)
        # Recent turns within the token-budget tail stay verbatim after the handoff
        tail_text = "\n".join(str(msg.get("content", "")) for msg in compressed[3:])
        self.assertIn("latest question", tail_text)

    def test_tier2_deterministic_fallback_when_llm_fails(self):
        engine = TrimtokContextEngine(
            context_length=10_000, threshold_percent=0.50, protect_first_n=1, protect_last_n=2,
            target_ratio=0.50, tail_budget_ratio=0.15, enable_llm_summary=True,
        )
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "start"}]
        for idx in range(12):
            msgs.append({"role": "user", "content": f"Fix bug {idx} in trimtok/context_engine.py " + ("x " * 150)})
            msgs.append({"role": "assistant", "content": f"Checked file {idx} " + ("y " * 150)})
        msgs.append({"role": "user", "content": "final"})
        msgs.append({"role": "assistant", "content": "ok"})

        fake_aux = types.ModuleType("agent.auxiliary_client")
        fake_aux.call_llm = MagicMock(side_effect=RuntimeError("aux down"))
        fake_aux.extract_content_or_reasoning = lambda resp: ""
        fake_agent_pkg = types.ModuleType("agent")

        with patch.dict(sys.modules, {"agent": fake_agent_pkg, "agent.auxiliary_client": fake_aux}):
            compressed = engine.compress(msgs, current_tokens=6000)

        self.assertLess(len(compressed), len(msgs))
        handoff = compressed[2]["content"]
        self.assertIn("deterministic", handoff)
        self.assertIn("## User Requests", handoff)
        self.assertIn("trimtok/context_engine.py", handoff)
        self.assertGreater(engine._llm_summary_cooldown_until, time.monotonic())

    def test_tier3_anti_thrash_breaker_and_preflight_deferral(self):
        engine = TrimtokContextEngine(context_length=100_000, threshold_percent=0.50, protect_first_n=2, protect_last_n=2)
        compressed = engine.compress(_messages())
        self.assertIsNot(compressed, None)
        # Immediately after compaction, rough preflight estimates defer until real usage arrives
        self.assertTrue(engine.should_defer_preflight_to_real_usage(60_000))

        # Strike 1: real provider prompt still >= threshold
        engine.update_from_response({"prompt_tokens": 55_000, "completion_tokens": 100, "total_tokens": 55_100})
        self.assertFalse(engine.should_defer_preflight_to_real_usage(60_000))
        self.assertEqual(engine.should_compress_info(55_000), (True, None))

        # Strike 2: second ineffective compaction trips breaker
        engine._verify_compaction_cleared_threshold = True
        engine.update_from_response({"prompt_tokens": 56_000, "completion_tokens": 100, "total_tokens": 56_100})
        self.assertEqual(engine.should_compress_info(56_000), (False, "ineffective"))
        self.assertFalse(engine.should_compress(56_000))

        # Session reset clears the breaker cleanly
        engine.on_session_reset()
        self.assertEqual(engine.should_compress_info(56_000), (True, None))

    def test_tier3_structural_noop_backoff_and_lift(self):
        engine = TrimtokContextEngine(context_length=100_000, threshold_percent=0.50, protect_first_n=2, protect_last_n=2)
        tiny = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
        out = engine.compress(tiny)
        self.assertIs(out, tiny)
        should, reason = engine.should_compress_info(60_000)
        self.assertFalse(should)
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("structural_backoff:"))

        # Lifting once transcript grows re-arms compression
        engine.lift_structural_backoff_if_grown(len(tiny) + 2)
        self.assertEqual(engine.should_compress_info(60_000), (True, None))

    def test_update_model_respects_model_thresholds(self):
        engine = TrimtokContextEngine(context_length=128_000, threshold_percent=0.50)
        engine.model_thresholds = {"gemini": 0.40}
        engine.update_model("gemini-2.5-pro", 1_000_000)
        self.assertEqual(engine.context_length, 1_000_000)
        self.assertAlmostEqual(engine.threshold_percent, 0.40)
        self.assertEqual(engine.threshold_tokens, 400_000)

    def test_ghost_skill_defense_marks_pruned_skill_view_in_tier1_and_tier2(self):
        engine = TrimtokContextEngine(
            context_length=10_000, threshold_percent=0.50, protect_first_n=1, protect_last_n=1,
            target_ratio=0.50, enable_llm_summary=False,
        )
        skill_body = "Step 1: follow strict SOP rules\n" * 120
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": "loading skill",
                "tool_calls": [
                    {"id": "sv_1", "type": "function", "function": {"name": "skill_view", "arguments": '{"name": "trimtok"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "sv_1", "name": "skill_view", "content": skill_body},
            {"role": "assistant", "content": "got it"},
            {"role": "user", "content": "next"},
        ]
        # Tier 1 stashing attaches canonical SKILL_PRUNED marker
        t1 = engine.compress(msgs)
        self.assertIn("[SKILL_PRUNED: content lost in compression; reload with skill_view(name='trimtok')]", t1[3]["content"])

        # Tier 2 folding preserves the SKILL_PRUNED marker in ## Pruned Skills
        long_msgs = msgs[:5] + [
            {"role": "user", "content": f"Turn {i} " + ("detail " * 150)} for i in range(6)
        ] + [{"role": "user", "content": "latest"}, {"role": "assistant", "content": "ok"}]
        t2 = engine.compress(long_msgs, current_tokens=6000)
        handoff = next(m for m in t2 if m.get("_compressed_summary"))
        self.assertIn("## Pruned Skills", handoff["content"])
        self.assertIn("reload with skill_view(name='trimtok')", handoff["content"])

    def test_head_decay_and_iterative_rolling_summary(self):
        engine = TrimtokContextEngine(
            context_length=10_000, threshold_percent=0.50, protect_first_n=2, protect_last_n=2,
            target_ratio=0.50, enable_llm_summary=True,
        )
        # Round 1 compaction
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "old opening 1"}, {"role": "assistant", "content": "old opening 2"}]
        for i in range(8):
            msgs.append({"role": "user", "content": f"Topic {i} " + ("alpha " * 120)})
            msgs.append({"role": "assistant", "content": f"Reply {i} " + ("beta " * 120)})
        msgs.extend([{"role": "user", "content": "recent 1"}, {"role": "assistant", "content": "recent 2"}])

        calls = []
        def _fake_call_llm(**kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "## Unified Checkpoint\n- Merged prior and new progress cleanly."}}]}

        fake_aux = types.ModuleType("agent.auxiliary_client")
        fake_aux.call_llm = _fake_call_llm
        fake_aux.extract_content_or_reasoning = lambda resp: resp["choices"][0]["message"]["content"]
        fake_agent_pkg = types.ModuleType("agent")

        with patch.dict(sys.modules, {"agent": fake_agent_pkg, "agent.auxiliary_client": fake_aux}):
            r1 = engine.compress(msgs, current_tokens=6000)
            self.assertEqual(engine.compression_count, 1)
            # Head still protected on round 1
            self.assertEqual(r1[1]["content"], "old opening 1")

            # Add more turns and trigger Round 2 -> head decays to 0 (system only) + rolling merge
            r2_in = list(r1)
            for i in range(8):
                r2_in.append({"role": "user", "content": f"Phase2 {i} " + ("gamma " * 120)})
                r2_in.append({"role": "assistant", "content": f"Phase2 reply {i} " + ("delta " * 120)})
            r2_in.extend([{"role": "user", "content": "latest user"}, {"role": "assistant", "content": "latest reply"}])

            r2 = engine.compress(r2_in, current_tokens=6000)

        self.assertEqual(engine.compression_count, 2)
        # After head decay, index 1 is now the unified rolling checkpoint (old opening 1 was folded in)
        self.assertTrue(r2[1].get("_compressed_summary"))
        self.assertIn("hybrid_llm_rolling", r2[1]["content"])
        self.assertEqual(len(calls), 2)
        self.assertIn("Previous Checkpoint:", calls[1]["messages"][1]["content"])

    def test_token_budget_tail_keeps_active_user_turn_and_caps_bloat(self):
        engine = TrimtokContextEngine(
            context_length=20_000, threshold_percent=0.50, protect_first_n=1, protect_last_n=6,
            tail_budget_ratio=0.15, enable_llm_summary=False,
        )
        # Short messages -> token budget keeps active user turn while folding older middle
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "init"}]
        for i in range(14):
            msgs.append({"role": "user", "content": f"Question {i} " + ("token " * 100)})
            msgs.append({"role": "assistant", "content": f"Answer {i} " + ("reply " * 100)})
        msgs.append({"role": "user", "content": "ACTIVE USER REQUEST"})
        msgs.append({"role": "assistant", "content": "working on it"})

        out = engine.compress(msgs, current_tokens=11_000)
        contents = [m.get("content", "") for m in out]
        self.assertIn("ACTIVE USER REQUEST", contents)
        self.assertLess(len(out), len(msgs))

    def test_background_prepare_ahead_splices_without_blocking_llm(self):
        engine = TrimtokContextEngine(
            context_length=10_000, threshold_percent=0.50, protect_first_n=1, protect_last_n=2,
            target_ratio=0.50, prepare_ahead_ratio=0.80, enable_llm_summary=True, enable_prepare_ahead=True,
        )
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "start"}]
        for i in range(8):
            msgs.append({"role": "user", "content": f"Work {i} " + ("data " * 120)})
            msgs.append({"role": "assistant", "content": f"Done {i} " + ("info " * 120)})
        msgs.extend([{"role": "user", "content": "recent"}, {"role": "assistant", "content": "ok"}])

        call_count = {"n": 0}
        def _fake_call_llm(**kwargs):
            call_count["n"] += 1
            return {"choices": [{"message": {"content": "## Prepared Summary\n- Pre-warmed in background."}}]}

        fake_aux = types.ModuleType("agent.auxiliary_client")
        fake_aux.call_llm = _fake_call_llm
        fake_aux.extract_content_or_reasoning = lambda resp: resp["choices"][0]["message"]["content"]
        fake_agent_pkg = types.ModuleType("agent")

        with patch.dict(sys.modules, {"agent": fake_agent_pkg, "agent.auxiliary_client": fake_aux}):
            # 4,200 is inside [80%, 100%) of 5,000 threshold -> triggers background prepare-ahead
            engine.on_turn_complete(msgs, usage={"prompt_tokens": 4200})
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                with engine._prepare_lock:
                    if engine._prepared_handoff is not None:
                        break
                time.sleep(0.02)

            self.assertEqual(call_count["n"], 1)
            # Now compress() crosses threshold -> splices prepared summary with ZERO additional LLM calls!
            out = engine.compress(msgs, current_tokens=5500)

        self.assertEqual(call_count["n"], 1)
        handoff = next(m for m in out if m.get("_compressed_summary"))
        self.assertIn("Pre-warmed in background", handoff["content"])

    def test_select_context_ccr_fts5_auto_recall_is_session_scoped_and_cache_safe(self):
        engine = TrimtokContextEngine(protect_first_n=1, protect_last_n=1, enable_auto_recall=True)

        # Put an unrelated payload into CCR from "another session"
        other_ref = ccr.put("def other_session_secret_fn():\n    return 'unrelated'\n" * 80)
        self.assertTrue(other_ref)

        # Stash a payload belonging to THIS session via compress()
        session_payload = "def reconcile_wallet_ledger():\n    # ledger_sync_v4\n" * 80
        msgs = _messages(session_payload)
        compressed = engine.compress(msgs)
        m = re.search(r"(#c[0-9a-zA-Z]+)", compressed[3]["content"])
        self.assertIsNotNone(m)
        session_ref = m.group(1)

        # User asks about `reconcile_wallet_ledger` -> appends [tt:recall ...] ONLY to trailing user msg
        req = list(compressed) + [{"role": "user", "content": "Check `reconcile_wallet_ledger` and `other_session_secret_fn` please"}]
        selected = engine.select_context(req, incoming_message=req[-1])
        self.assertIsNotNone(selected)
        # Prefix is untouched (same objects) -> prompt cache stays 100% intact
        self.assertEqual(selected[:-1], req[:-1])
        # Original req[-1] was not mutated
        self.assertNotIn("[tt:recall", req[-1]["content"])
        # Session ref is recalled, cross-session ref is NOT leaked
        self.assertIn("[tt:recall", selected[-1]["content"])
        self.assertIn(session_ref, selected[-1]["content"])
        self.assertNotIn(other_ref, selected[-1]["content"])

        # Idempotent on retry -> returns None
        self.assertIsNone(engine.select_context(selected, incoming_message=selected[-1]))

        # Mid-tool-loop (last message is tool, not user) -> returns None
        tool_tail = list(compressed) + [{"role": "tool", "tool_call_id": "c9", "content": "ok"}]
        self.assertIsNone(engine.select_context(tool_tail, incoming_message=req[-1]))

        # Reset clears session refs
        engine.on_session_reset()
        fresh_req = [{"role": "system", "content": "s"}, {"role": "user", "content": "Check `reconcile_wallet_ledger`"}]
        self.assertIsNone(engine.select_context(fresh_req, incoming_message=fresh_req[-1]))


if __name__ == "__main__":
    unittest.main()
