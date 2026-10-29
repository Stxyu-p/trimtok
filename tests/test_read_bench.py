"""Tests for multi-approach read_file benchmark runner."""
from __future__ import annotations

import unittest
from pathlib import Path

# Import runner functions (will fail until runner is created)
from eval.eval_read_file_bench import (
    BenchmarkMetric,
    evaluate_strategy_on_payload,
    verify_patch_safety,
)

class TestReadBenchmark(unittest.TestCase):
    def test_metric_calculation(self):
        m = BenchmarkMetric(
            before_chars=1000,
            after_chars=600,
            latency_ms=4.5,
            patch_safe=True,
            kept_functions=5,
            folded_functions=2,
        )
        self.assertEqual(m.saved_chars, 400)
        self.assertAlmostEqual(m.reduction_pct, 40.0)

    def test_patch_safety_detector(self):
        original = "1|def add(a, b):\n2|    return a + b\n3|\n4|def sub(a, b):\n5|    return a - b"
        # Preserved line prefixes
        valid_transformed = "1|def add(a, b):\n2|    return a + b\n4|def sub(a, b):\n5|    return a - b"
        # Invalid / shifted line prefixes
        invalid_transformed = "def add(a, b):\n    return a + b"
        
        self.assertTrue(verify_patch_safety(original, valid_transformed))
        self.assertFalse(verify_patch_safety(original, invalid_transformed))

    def test_evaluate_dummy_strategy(self):
        payload = {
            "id": "test-dummy",
            "content": "1|line1\n2|line2\n3|line3",
            "domain": "code-small",
            "lang": "python",
        }
        def dummy_strategy(content: str, meta: dict):
            # Drops line 2 but preserves line numbers
            return "1|line1\n3|line3", {"gate": "ok"}

        metric = evaluate_strategy_on_payload(dummy_strategy, payload)
        self.assertEqual(metric.before_chars, len(payload["content"]))
        self.assertTrue(metric.patch_safe)
        self.assertGreater(metric.saved_chars, 0)

if __name__ == "__main__":
    unittest.main()
