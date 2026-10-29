"""Tests for RTK / Repomix pure signature stripping strategy."""
from __future__ import annotations

import unittest
from trimtok.strategies.rtk_strip import strip_signatures_rtk

class TestRTKStrategy(unittest.TestCase):
    def test_python_strips_all_function_bodies(self):
        code = (
            "1|import math\n"
            "2|\n"
            "3|def calculate_area(radius: float) -> float:\n"
            "4|    # Compute circle area\n"
            "5|    return math.pi * radius * radius\n"
            "6|\n"
            "7|def greet(name: str) -> str:\n"
            "8|    return f'Hello, {name}'\n"
        )
        out, stats = strip_signatures_rtk(code, {"lang": "python"})
        self.assertIn("def calculate_area(radius: float) -> float:", out)
        self.assertIn("def greet(name: str) -> str:", out)
        # Function bodies must be stripped
        self.assertNotIn("math.pi * radius * radius", out)
        self.assertNotIn("return f'Hello, {name}'", out)
        self.assertEqual(stats["kept_functions"], 0)
        self.assertEqual(stats["folded_functions"], 2)
        self.assertGreater(stats["saved_chars"], 0)

    def test_js_ts_strips_function_bodies(self):
        js = (
            "1|function processOrder(orderId, items) {\n"
            "2|    const total = items.reduce((a, b) => a + b.price, 0);\n"
            "3|    return { id: orderId, total };\n"
            "4|}\n"
        )
        out, stats = strip_signatures_rtk(js, {"lang": "javascript"})
        self.assertIn("function processOrder(orderId, items)", out)
        self.assertNotIn("items.reduce", out)
        self.assertEqual(stats["folded_functions"], 1)

if __name__ == "__main__":
    unittest.main()
