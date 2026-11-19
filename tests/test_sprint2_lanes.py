"""Sprint 2 unit & regression tests:
1. Wide-line folding for 5-120 line non-JSON/diff terminal outputs with #ref + fault safety
2. Terminal search lane (`kind="search"`) grouping multi-file and single-file grep hits
3. Dedicated `web_search` lane cleaning tracking URLs, dropping backend noise, trimming long snippets
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-sprint2-"))

from trimtok import ccr, core
from trimtok.dedup import compress_tool_result


class TestSprint2Lanes(unittest.TestCase):
    def test_wide_line_folder_compresses_sub120_line_outputs_and_preserves_faults(self) -> None:
        wide_a = "record_alpha=" + ("x" * 1750) + "_end_alpha"
        wide_b = "record_beta=" + ("y" * 1750) + "_end_beta"
        lines = [f"header_line_{i}: ok" for i in range(10)] + [wide_a, wide_b] + [
            f"footer_line_{i}: done" for i in range(10)
        ]
        raw = "\n".join(lines)
        self.assertGreater(len(raw), 3500)
        self.assertLess(len(lines), 120)

        out, stats = core.compress_terminal("dump-records", raw, min_chars=2048)
        self.assertEqual(stats["gate"], "ok")
        self.assertLess(len(out), len(raw) * 0.5)
        self.assertIn("record_alpha=", out)
        self.assertIn("_end_alpha", out)
        self.assertIn("chars in #", out)

        # Fault token inside the middle of a wide line must NOT be hidden
        mid_fault = ("prefix_" * 60) + " CRITICAL_DATABASE_FAULT: connection refused " + ("suffix_" * 60)
        raw_fault = "\n".join([f"step_{i}: ok" for i in range(15)] + [mid_fault] + [f"tail_{i}: ok" for i in range(10)])
        out_f, _ = core.compress_terminal("check-db", raw_fault, min_chars=2048)
        self.assertIn("CRITICAL_DATABASE_FAULT: connection refused", out_f)

        # Giant 1-4 line blobs (>=8192 chars) must still use pointer_large
        giant_blob = "A" * 10000
        out_g, st_g = core.compress_terminal("cat blob.bin", giant_blob, min_chars=2048)
        self.assertEqual(st_g["gate"], "ok")
        self.assertEqual(st_g["kind"], "pointer_large")
        self.assertIn("[tt:pointer_large", out_g)

    def test_terminal_search_lane_groups_grep_hits(self) -> None:
        grep_lines = []
        for f_idx in range(3):
            for h_idx in range(15):
                grep_lines.append(
                    f"src/module_{f_idx}.py:{10 + h_idx * 3}:    value_{h_idx} = process_item(config_{h_idx}, mode='fast')"
                )
        raw_grep = "\n".join(grep_lines)
        out, stats = core.compress_terminal("grep -rn process_item src/", raw_grep, min_chars=1024)
        self.assertEqual(stats["gate"], "ok")
        self.assertEqual(stats["kind"], "search")
        self.assertIn("=== src/module_0.py (15 hits) ===", out)
        self.assertIn("[@#", out)
        self.assertLess(len(out), len(raw_grep) * 0.65)

        # Single-file dense grep output (>=15 hits)
        single_lines = [
            f"trimtok/core.py:{20 + i * 4}:    result_{i} = compute_metric(alpha={i}, beta={i + 1})"
            for i in range(25)
        ]
        raw_single = "\n".join(single_lines)
        out_s, st_s = core.compress_terminal("grep -n compute_metric trimtok/core.py", raw_single, min_chars=1024)
        self.assertEqual(st_s["gate"], "ok")
        self.assertIn("=== trimtok/core.py (25 hits) ===", out_s)
        self.assertLess(len(out_s), len(raw_single) * 0.55)

    def test_web_search_lane_cleans_urls_noise_and_trims_long_descriptions(self) -> None:
        payload_obj = {
            "success": True,
            "data": {
                "web": [
                    {
                        "url": f"http://localhost:8080/article-{i}?q=python&utm_source=google&utm_medium=cpc&fbclid=abc123",
                        "title": f"Python Guide Part {i}",
                        "description": (
                            f"Section {i} overview with [inline link](http://localhost:8080/long/tracking/path/{i}) "
                            + ("detailed benchmark explanation and configuration notes " * 25)
                        ),
                        "position": i + 1,
                    }
                    for i in range(6)
                ],
                "served_by": "brave-edge-01",
                "rescued_from": "none",
                "backend_error": "transient fallback notice that wastes tokens",
            },
        }
        raw = json.dumps(payload_obj, indent=2)
        self.assertGreater(len(raw), 8000)

        out, stats = compress_tool_result("web_search", raw, session_id="s_ws", min_chars=2048)
        self.assertEqual(stats["gate"], "ok")
        self.assertEqual(stats["kind"], "web_search_folded")
        self.assertLess(len(out), len(raw) * 0.45)

        self.assertNotIn("utm_source", out)
        self.assertNotIn("fbclid", out)
        self.assertIn("?q=python", out)
        self.assertNotIn("served_by", out)
        self.assertNotIn("backend_error", out)
        self.assertIn("trimtok_retrieve #c", out)

        m = re.search(r"#(c[0-9A-Za-z]+)", out)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(ccr.get("#" + m.group(1)), raw)


if __name__ == "__main__":
    unittest.main()
