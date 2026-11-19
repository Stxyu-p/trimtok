"""Sprint 3 hardening tests:
1. Bounded read_file: 512KB AST cap (1.6MB file completes <200ms as file_large_pointer) + mid-AST Deadline guard
2. CCR chunks_fts 16KB plaintext cap while keeping full lossless Zstd storage & symbol/fault searchability
3. TrimtokContextEngine stashed stub preview line & tool name label
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-sprint3-"))

from trimtok import ccr
from trimtok.context_engine import TrimtokContextEngine
from trimtok.dedup import compress_tool_result
from trimtok.pruners import smart_skeletonize_code
from trimtok.signals import Deadline


class TestSprint3Hardening(unittest.TestCase):
    def test_read_file_512kb_cap_bounds_latency_and_preserves_full_ccr(self) -> None:
        # Build a ~1.6 MB Python source file (> 524,288 bytes)
        fn_block = (
            "def compute_batch_metrics(records, threshold=0.75):\n"
            + "".join(f"    acc_{j} = (threshold * {j}) + len(records)\n" for j in range(18))
            + "    return acc_0\n\n"
        )
        raw_src = fn_block * 2200
        self.assertGreater(len(raw_src), 1_500_000)

        lines = raw_src.splitlines()
        numbered = "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(lines))
        payload = json.dumps({"content": numbered, "total_lines": len(lines)})

        # Warm the store so we measure steady-state hook latency, not one-time SQLite DDL
        ccr.put("warmup")
        t0 = time.perf_counter()
        out, stats = compress_tool_result(
            "read_file",
            payload,
            args={"path": "src/generated_big.py", "offset": 1, "limit": 50000},
            session_id="s3_big_file",
            deadline=Deadline(200.0),
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertEqual(stats["gate"], "ok")
        self.assertEqual(stats["kind"], "file_large_pointer")
        self.assertLess(elapsed_ms, 100.0)
        self.assertLess(len(out), 10_000)
        self.assertIn("[tt:file_large #c", out)

        m = re.search(r"#(c[0-9A-Za-z]+)", out)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(ccr.get("#" + m.group(1)), payload)

    def test_ast_skeletonizer_honors_expired_deadline(self) -> None:
        src = "\n\n".join(
            f"def func_{i}(x):\n" + "\n".join(f"    v_{j} = {j}" for j in range(20)) + "\n    return v_0"
            for i in range(40)
        )
        expired = Deadline(1.0)
        time.sleep(0.01)
        with self.assertRaises(TimeoutError):
            smart_skeletonize_code(src, deadline=expired)

        numbered = "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(src.splitlines()))
        out, stats = compress_tool_result(
            "read_file",
            numbered,
            args={"path": "src/slow_mod.py", "offset": 1, "limit": 2000},
            session_id="s3_timeout",
            deadline=expired,
        )
        self.assertEqual(out, numbered)
        self.assertEqual(stats["gate"], "passthrough_reference")
        self.assertEqual(stats["reason"], "skeleton_budget")

    def test_ccr_fts_capped_at_16kb_while_full_payload_remains_lossless(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = ccr.FastCCRStore(Path(td))
            try:
                head_marker = "HEAD_UNIQUE_TOKEN_ALPHA"
                mid_def = "def middle_critical_handler_omega(event):\n    raise RuntimeError('MID_FAULT_SIGMA')"
                tail_marker = "TAIL_UNIQUE_TOKEN_ZETA"
                filler = ("0123456789abcdef_neutral_padding_line\n" * 1600)  # ~60 KB
                big_payload = f"{head_marker}\n{filler}\n{mid_def}\n{filler}\n{tail_marker}"
                self.assertGreater(len(big_payload), 120_000)

                alias = store.put(big_payload)
                self.assertTrue(alias.startswith("#c"))

                # Full original must be 100% intact
                self.assertEqual(store.get(alias), big_payload)

                # FTS5 index size must be capped at <= 16,384 bytes
                st = store.stats()
                self.assertLessEqual(st["fts_bytes"], 16_384)

                # Head, tail, and middle symbol/fault lines remain searchable
                self.assertEqual([h["alias"] for h in store.search(head_marker)], [alias])
                self.assertEqual([h["alias"] for h in store.search(tail_marker)], [alias])
                self.assertEqual([h["alias"] for h in store.search("middle_critical_handler_omega")], [alias])
                self.assertEqual([h["alias"] for h in store.search("MID_FAULT_SIGMA")], [alias])
            finally:
                store.close()

    def test_context_engine_stub_includes_tool_name_and_preview(self) -> None:
        engine = TrimtokContextEngine(protect_first_n=1, protect_last_n=1, min_stash_chars=512)
        long_first_line = "SELECT id, customer_name, total_amount, status FROM orders WHERE region = 'APAC' ORDER BY created_at DESC;"
        body = f"\n\n{long_first_line}\n" + ("row_value_1234567890\n" * 80)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "tool", "name": "query_database", "tool_call_id": "c1", "content": body},
            {"role": "assistant", "content": "a1"},
        ]
        compacted = engine.compress(messages)
        stub = compacted[2]["content"]
        self.assertTrue(stub.startswith("[tt:stashed query_database result ("))
        self.assertIn("\nPreview: ", stub)
        preview_line = stub.split("\nPreview: ", 1)[1]
        self.assertLessEqual(len(preview_line), 80)
        self.assertTrue(preview_line.startswith("SELECT id, customer_name"))

        m = re.search(r"(#c[0-9A-Za-z]+)", stub)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(ccr.get(m.group(1)), body)


if __name__ == "__main__":
    unittest.main()
