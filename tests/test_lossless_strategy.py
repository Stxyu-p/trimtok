"""Tests for lossless cosmetic minification strategy (Baseline Strategy B)."""
from __future__ import annotations

import unittest
from trimtok.strategies.lossless_min import minify_lossless

class TestLosslessStrategy(unittest.TestCase):
    def test_cleans_license_and_banners_without_touching_logic(self):
        code = (
            "1|/**\n"
            "2| * MIT License\n"
            "3| * Copyright (c) 2026 Foo Corp\n"
            "4| * All rights reserved.\n"
            "5| */\n"
            "6|\n"
            "7|// =========================================\n"
            "8|// Helpers section\n"
            "9|// =========================================\n"
            "10|\n"
            "11|\n"
            "12|function add(a, b) {\n"
            "13|    return a + b;\n"
            "14|}\n"
        )
        out, stats = minify_lossless(code, {"lang": "javascript"})
        # License & banner lines stripped
        self.assertNotIn("MIT License", out)
        self.assertNotIn("Copyright (c) 2026", out)
        self.assertNotIn("=========================================", out)
        # Logic preserved 100%
        self.assertIn("function add(a, b) {", out)
        self.assertIn("return a + b;", out)
        self.assertEqual(stats["folded_functions"], 0)
        self.assertEqual(stats["kept_functions"], 1)
        self.assertGreater(stats["saved_chars"], 0)

if __name__ == "__main__":
    unittest.main()
