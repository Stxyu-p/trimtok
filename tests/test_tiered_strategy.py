"""Tests for Tiered Semantic Shaving with Line Anchors (Strategy C)."""
from __future__ import annotations

import unittest
from trimtok.strategies.tiered_shave import shave_tiered

class TestTieredStrategy(unittest.TestCase):
    def test_small_code_keeps_all_logic_and_cleans_license(self):
        code = (
            "1|// Copyright (c) 2026 Test Suite\n"
            "2|// All rights reserved.\n"
            "3|// ==============================\n"
            "4|\n"
            "5|def helper_one(x):\n"
            "6|    return x * 2\n"
            "7|\n"
            "8|def helper_two(y):\n"
            "9|    return y + 10\n"
        )
        out, stats = shave_tiered(code, {"lang": "python", "domain": "code-small"})
        self.assertNotIn("Copyright (c) 2026", out)
        self.assertNotIn("==============================", out)
        # All short functions kept
        self.assertIn("def helper_one(x):", out)
        self.assertIn("return x * 2", out)
        self.assertIn("def helper_two(y):", out)
        self.assertIn("return y + 10", out)
        self.assertEqual(stats["kept_functions"], 2)
        self.assertEqual(stats["folded_functions"], 0)

    def test_large_code_preserves_short_functions_and_folds_large_ones(self):
        # 1 short helper (3 lines) + 1 large handler (35 lines)
        large_body = "\n".join([f"{i}|    step_{i} = do_work({i})" for i in range(12, 50)])
        code = (
            "1|def short_helper(x):\n"
            "2|    return x + 1\n"
            "3|\n"
            "4|def large_handler(data):\n"
            "5|    \"\"\"Process full request pipeline.\"\"\"\n"
            + large_body + "\n"
            "51|    return final_result\n"
        )
        out, stats = shave_tiered(code, {"lang": "python", "domain": "code-large"})
        # Short helper is KEPT for single-turn comprehension
        self.assertIn("def short_helper(x):", out)
        self.assertIn("return x + 1", out)
        self.assertEqual(stats["kept_functions"], 1)
        # Large handler signature and docstring kept, body folded
        self.assertIn("def large_handler(data):", out)
        self.assertIn('"""Process full request pipeline."""', out)
        self.assertNotIn("step_20 = do_work(20)", out)
        self.assertEqual(stats["folded_functions"], 1)
        self.assertIn("[tt:folded", out)

    def test_web_doc_prunes_cookie_and_nav_boilerplate(self):
        doc = (
            "# My Article Title\n"
            "Cookie Notice: We use tracking cookies. [Accept All]\n"
            "Navigation: Home > Tech > Article\n"
            "\n"
            "## Section 1: Real Information\n"
            "This is crucial technical detail that the user requested.\n"
            "\n"
            "Share on Twitter | Facebook | LinkedIn\n"
        )
        out, stats = shave_tiered(doc, {"lang": "markdown", "domain": "web-doc"})
        self.assertNotIn("Cookie Notice", out)
        self.assertNotIn("Share on Twitter", out)
        self.assertIn("## Section 1: Real Information", out)
        self.assertIn("This is crucial technical detail", out)

if __name__ == "__main__":
    unittest.main()
