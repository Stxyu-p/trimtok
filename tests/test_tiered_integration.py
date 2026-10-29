"""Integration tests for Tiered Intake inside compress_tool_result."""
from __future__ import annotations

import unittest
from trimtok.dedup import compress_tool_result

class TestTieredIntegration(unittest.TestCase):
    def test_web_extract_compresses_and_debloats_links(self):
        raw_web = (
            "# Research Paper Overview\n"
            "Cookie Notice: We use tracking cookies. [Accept All] [Reject]\n"
            "Navigation: Home > Science > Research\n"
            "\n"
            "## Key Findings\n"
            "The experiment demonstrated significant [lossless compression](https://en.wikipedia.org/wiki/Lossless_compression 'Wiki') "
            "using advanced [statistical modeling](https://en.wikipedia.org/wiki/Statistical_model) across all metrics.\n"
            "\n"
            "Share on Twitter | Facebook | LinkedIn\n"
        ) * 5  # Make it > 1000 chars

        res, stats = compress_tool_result("web_extract", raw_web, min_chars=500)
        self.assertEqual(stats["gate"], "ok")
        self.assertNotIn("Cookie Notice", res)
        self.assertNotIn("Share on Twitter", res)
        # Link URL targets stripped, anchor text preserved
        self.assertNotIn("https://en.wikipedia.org/wiki/Lossless_compression", res)
        self.assertIn("lossless compression", res)
        self.assertGreater(stats["saved_chars"], 0)

    def test_read_file_small_code_cleans_license_safely(self):
        code = (
            "1|/**\n"
            "2| * MIT License\n"
            "3| * Copyright (c) 2026 Test Suite\n"
            "4| * All rights reserved.\n"
            "5| */\n"
            "6|// ======================================\n"
            "7|def calculate_sum(a: int, b: int) -> int:\n"
            "8|    return a + b\n"
        )
        res, stats = compress_tool_result(
            "read_file", code, args={"path": "calc.py", "offset": 1, "limit": 2000}, min_chars=100
        )
        self.assertEqual(stats["gate"], "ok")
        self.assertNotIn("MIT License", res)
        self.assertNotIn("======================================", res)
        self.assertIn("def calculate_sum", res)
        self.assertIn("return a + b", res)

if __name__ == "__main__":
    unittest.main()
