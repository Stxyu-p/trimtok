"""Focused regression tests for the full-project hardening & audit pass."""
from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trimtok import (
    ccr,
    context_engine,
    core,
    dedup,
    filters,
    guard,
    json_envelope,
    kv_lock,
    mcp_install,
    mcp_proxy,
    mcp_server,
    policy,
    prerewrite,
    pruners,
    report,
    signals,
    toon_codec,
    ts_skeleton,
)
from trimtok.strategies import lossless_min, rtk_strip, tiered_shave


class TestWave1Hardening(unittest.TestCase):
    def test_prerewrite_git_log_places_flags_before_path(self):
        out = prerewrite.rewrite_argv(["git", "log", "README.md"])
        self.assertEqual(out, ["git", "log", "--oneline", "-n", "25", "README.md"])

    def test_kv_lock_does_not_eat_lowercase_words_after_time(self):
        prompt = "Meeting at 14:30:00 for the team.\nNext line."
        res = kv_lock.stabilize_prompt(prompt)
        self.assertIn("<time> for the team.", res)
        self.assertIn("14:30:00", res)

    def test_kv_lock_handles_multiple_appendices_and_fences(self):
        text = (
            "```md\n<!-- kv-lock: dynamic context -->\nfake: 1\n```\n"
            "Body\n\n<!-- kv-lock: dynamic context -->\ndate: 2026-01-01\n\n"
            "<!-- kv-lock: dynamic context -->\ntime: 10:00:00\n"
        )
        res = kv_lock.stabilize_prompt(text)
        self.assertIn("```md\n<!-- kv-lock: dynamic context -->\nfake: 1\n```", res)
        self.assertEqual(res.count("date: 2026-01-01"), 1)
        self.assertEqual(res.count("time: 10:00:00"), 1)

    def test_policy_model_family_and_nan_pressure(self):
        self.assertEqual(policy.model_family_of("openai/o3-mini"), "openai")
        self.assertEqual(policy.model_family_of("anthropic/claude-sonnet-4"), "anthropic")
        self.assertEqual(policy.model_family_of("z-ai/glm-5"), "glm")
        self.assertEqual(policy.level_for_pressure(float("nan")), "normal")
        self.assertEqual(policy.level_for_pressure(float("inf")), "normal")
        self.assertFalse(policy.looks_secret(None))  # type: ignore[arg-type]

    def test_policy_terminal_git_diff_not_classified_as_edit_patch(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        pol_term = policy.classify(diff, tool="terminal")
        self.assertFalse(pol_term.verbatim)
        pol_edit = policy.classify(diff, tool="patch")
        self.assertTrue(pol_edit.verbatim)

    def test_guard_catches_context_engine_stashed_stub_and_skips_fences(self):
        stub = "[tt:stashed 12000c of terminal -> trimtok_retrieve '#c1' or section='errors']"
        hits = guard.find_stub_markers(stub)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "tt_stashed_stub")

        fenced = f"```text\n{stub}\n```\n"
        self.assertEqual(guard.find_stub_markers(fenced), [])

        # JSON-encoded args in check_write_args
        blk = guard.check_write_args("replace_file_content", json.dumps({"ReplacementContent": stub}))
        self.assertIsNotNone(blk)
        assert blk is not None
        self.assertTrue(blk["blocked"])

    def test_context_engine_ref_re_single_char_and_secret_policy(self):
        self.assertEqual(context_engine._REF_RE.findall("see #c1 and #cZ and #c24O"), ["#c1", "#cZ", "#c24O"])
        eng = context_engine.TrimtokContextEngine(policy_enabled=True, min_stash_chars=50)
        secret_content = "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE\n" + ("x" * 200)
        self.assertFalse(eng._should_stash(secret_content))

    def test_context_engine_update_from_response_ignores_zero_tokens(self):
        eng = context_engine.TrimtokContextEngine()
        eng.last_prompt_tokens = 42000
        eng.awaiting_real_usage_after_compression = True
        eng.update_from_response({"prompt_tokens": 0, "completion_tokens": 10})
        self.assertEqual(eng.last_prompt_tokens, 42000)
        self.assertTrue(eng.awaiting_real_usage_after_compression)


class TestWave2Hardening(unittest.TestCase):
    def test_toon_codec_no_shared_mutable_list_and_superscript_and_rows_key(self):
        # Shared mutable container bug in dcache
        rows = [{"id": i, "tags": [1, 2]} for i in range(6)]
        enc = toon_codec.encode(rows)
        if enc is not None:
            dec = toon_codec.toon_decode(enc)
            assert isinstance(dec, list)
            dec[0]["tags"].append(99)
            self.assertEqual(dec[1]["tags"], [1, 2])

        # Superscript digits should stay strings without crashing int()
        sup_rows = [{"id": i, "label": "1²"} for i in range(5)]
        enc_sup = toon_codec.encode(sup_rows)
        if enc_sup is not None:
            dec_sup = toon_codec.toon_decode(enc_sup)
            self.assertEqual(dec_sup, sup_rows)

        # Single-key {"rows": [...]} roundtrip preserves wrapper key
        wrapped = {"rows": [{"a": i, "b": "x"} for i in range(6)]}
        enc_w = toon_codec.encode(wrapped)
        if enc_w is not None:
            self.assertEqual(toon_codec.toon_decode(enc_w), wrapped)

    def test_ccr_small_max_bytes_does_not_evict_newly_inserted_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ccr.FastCCRStore(Path(tmp))
            try:
                payload = "important payload " * 100
                ref = store.put(payload, max_bytes=10)
                self.assertTrue(ref.startswith("#c"))
                self.assertEqual(store.get(ref), payload)
                # Lone surrogate never raises
                sur = "hello \ud800 world"
                sref = store.put(sur)
                self.assertTrue(sref.startswith("#c"))
                self.assertIsInstance(store.get(sref), str)
            finally:
                store.close()

    def test_ts_skeleton_preserves_inline_brace_and_tabs(self):
        py_tab = "def f():\n" + ("\tx = 1\n" * 15) + "\treturn x\n"
        out, _saved, _cnt = ts_skeleton.skeletonize_code_full(py_tab, filename="t.py", min_body_lines=8, ref="#c1")
        # Must compile without TabError!
        compile(out, "t.py", "exec")

    def test_json_envelope_single_odd_row_does_not_veto_uniform_bucket(self):
        items = [{"id": i, "name": f"user_{i}", "role": "member"} for i in range(12)]
        items.append({"id": 99, "name": "odd", "extra": True})
        raw = json.dumps({"ok": True, "items": items})
        res = json_envelope.fold_envelope_array(raw)
        self.assertIsNotNone(res)
        assert res is not None
        folded, _meta = res
        self.assertLess(len(folded), len(raw))


class TestWave3Hardening(unittest.TestCase):
    def test_signals_benign_zero_boundary_and_js_stack_and_bare_exc(self):
        # Must NOT suppress 10 errors / 20 failures / 500 error!
        self.assertTrue(signals.is_important("10 errors found in build"))
        self.assertTrue(signals.is_important("20 failures in test suite"))
        self.assertTrue(signals.is_important("HTTP 500 error on /api"))
        # True zero errors stays benign
        self.assertFalse(signals.is_important("0 errors, 0 warnings"))
        # JS stack frame preserves indentation check
        self.assertTrue(signals.is_important("    at processTicks (node:internal/process/task_queues:95:5)"))
        # Bare exception name without colon
        self.assertTrue(signals.is_important("ValueError"))
        self.assertTrue(signals.is_important("NullPointerException"))
        # Pass verdict with leading [OK]
        self.assertFalse(signals.is_important("[OK] handles error case"))

    def test_filters_git_status_conflicts_and_tracked_count(self):
        # Merge conflicts must NOT be summarized away (returns None -> preserved verbatim)
        conflict_raw = (
            "On branch main\n"
            "Unmerged paths:\n"
            "\tboth modified:   conflict.py\n"
            "Changes not staged for commit:\n"
            "\tmodified:   app.py\n"
            "Untracked files:\n"
            "\tnew.py\n"
        )
        self.assertIsNone(filters.try_command_filter("git status", conflict_raw))

        # Clean status with tracked + untracked must not double-count modified lines as untracked
        normal_raw = (
            "On branch main\n"
            "Changes not staged for commit:\n"
            "\tmodified:   app.py\n"
            "\tmodified:   util.py\n"
            "Untracked files:\n"
            "\tnew.py\n"
        )
        out = filters.try_command_filter("git status", normal_raw)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("2 modified", out)
        self.assertIn("1 untracked", out)

    def test_lossless_min_preserves_jsdoc_and_rtk_single_line_fn(self):
        js = "/** Important JSDoc */\nfunction add(a, b) { return a + b; }\n"
        out, _ = lossless_min.minify_lossless(js, {"lang": "js"})
        self.assertIn("Important JSDoc", out)

        js_code = "function f() { return 1; }\nfunction g() {\n  const a = 1;\n  const b = 2;\n  return a + b;\n}\n"
        stripped, _meta = rtk_strip.strip_signatures_rtk(js_code, {"lang": "javascript"})
        self.assertIn("function g()", stripped)

    def test_pruners_python_311_caret_lines_preserved(self):
        tb = (
            "Traceback (most recent call last):\n"
            + "".join(f'  File "f{i}.py", line {i}, in fn{i}\n    call_{i}()\n' for i in range(12))
            + '  File "leaf.py", line 99, in boom\n'
            + "    x = a + b\n"
            + "        ~~^~~\n"
            + "TypeError: unsupported operand\n"
        )
        pruned_lines = pruners.prune_python_traceback(tb.splitlines())
        pruned = "\n".join(pruned_lines)
        self.assertIn("~~^~~", pruned)
        self.assertIn("TypeError: unsupported operand", pruned)

    def test_dedup_fold_spans_prefers_multiline_and_keeps_errors(self):
        sid = "test-hardening-dedup-span"
        dedup.clear_session(sid)
        block = "\n".join(f"normal repeated output line number {i} with enough padding chars" for i in range(8))
        dedup.remember(block, sid, owner="aabbccddeeff")
        folded, _, saved = dedup.fold_spans(block, sid, owner="aabbccddeeff")
        self.assertGreater(saved, 0)
        # Collapsed into 1 multi-line pointer, not 8 single-line pointers
        self.assertEqual(len(folded.splitlines()), 1)
        dedup.clear_session(sid)


class TestWave4Hardening(unittest.TestCase):
    def test_mcp_proxy_lf_headers_and_content_type_first_and_empty_instructions(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "instructions": "old"}})
        raw = f"Content-Type: application/json\nContent-Length: {len(body)}\n\n{body}".encode("utf-8")
        filtered = mcp_proxy.filter_mcp_message(raw, instructions="")
        self.assertIn(b"\n\n", filtered)
        self.assertNotIn(b"\r\n\r\n", filtered)
        _, _, rest = filtered.partition(b"\n\n")
        self.assertEqual(json.loads(rest.decode("utf-8"))["result"]["instructions"], "")

    def test_mcp_server_negative_purge_does_not_wipe_and_unknown_action_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"TRIMTOK_DATA_DIR": tmp}):
                ccr.close()
                try:
                    ref = ccr.put("keep me safe " * 100)
                    res_neg = mcp_server._tool_status({"action": "purge", "older_than_hours": -5})
                    self.assertIn("error", res_neg)
                    # Payload MUST still exist!
                    self.assertEqual(ccr.get(ref), "keep me safe " * 100)

                    res_bad = mcp_server._tool_status({"action": "does_not_exist"})
                    self.assertIn("error", res_bad)
                finally:
                    ccr.close()

    def test_mcp_server_usage_row_passthrough_records_raw_len(self):
        row = mcp_server._usage_row("terminal", "echo", {"gate": "passthrough_small"}, raw_len=321)
        self.assertEqual((row["before"], row["after"], row["saved"]), (321, 321, 0))

    def test_mcp_server_terminal_file_path_honors_security_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            sec = Path(tmp) / "security" / "policy.yaml"
            sec.parent.mkdir()
            sec.write_text("allow:\n" + ("rule: allow\n" * 300), encoding="utf-8")
            resp = mcp_server._tool_compress_terminal({"file_path": str(sec)})
            self.assertEqual(resp["stats"]["gate"], "passthrough_policy")
            self.assertEqual(resp["stats"]["reason"], "security_path")

    def test_report_normalize_row_sizes_invariants(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "usage.jsonl"
            p.write_text(
                json.dumps({"hook": "terminal", "gate": "ok", "before": 1000, "after": 400, "saved": 9999, "shape": "log"}) + "\n"
                + json.dumps({"hook": "terminal", "gate": "passthrough_small", "before": 200, "after": 0, "saved": 200, "shape": "small"}) + "\n"
                + json.dumps({"hook": "terminal", "gate": "ok", "before": "bad", "after": None, "saved": "oops", "shape": "log"}) + "\n",
                encoding="utf-8",
            )
            txt = report.gain_summary(p)
            self.assertIn("1,200 -> 600 chars", txt)
            self.assertIn("600 chars", txt)

    def test_mcp_install_toml_ignores_header_inside_multiline_string(self):
        toml_text = (
            'desc = """\n'
            "[mcp_servers.trimtok]\n"
            'not a real table\n'
            '"""\n\n'
            "[mcp_servers.other]\n"
            'command = "keep"\n'
        )
        stripped, found = mcp_install._strip_toml_table(toml_text, "mcp_servers.trimtok")
        self.assertFalse(found)
        self.assertEqual(stripped, toml_text)


if __name__ == "__main__":
    unittest.main()
