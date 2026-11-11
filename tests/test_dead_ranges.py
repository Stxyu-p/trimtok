"""Unit tests for Dead-Range Incompressibility Cache (billion-context pattern) in trimtok."""

import unittest
from unittest import mock
from trimtok.core import compress_terminal, _DEAD_RANGES


def _huge_header(*_a, **_kw):
    return ["h" * 4000]


class TestDeadRanges(unittest.TestCase):
    def test_uncompressible_cached_subsequent_call(self):
        # Clear cache for isolated test
        _DEAD_RANGES.clear()

        raw = "\n".join(f"arbitrary item line {i:03d} with uncompressed data" for i in range(120))

        # First run: header cost forces revert
        with mock.patch("trimtok.core.adaptive_terminal_head", side_effect=_huge_header), \
             mock.patch("trimtok.core.is_important", return_value=False), \
             mock.patch("trimtok.core.is_exempt", return_value=False), \
             mock.patch("trimtok.core._FUZZY_AVAILABLE", False), \
             mock.patch("trimtok.core.estimate_tokens", return_value=1):
            out1, st1 = compress_terminal("build", raw)

        self.assertEqual(st1["gate"], "reverted_gate")
        self.assertFalse(st1.get("cached_dead_range", False))

        # Second run: dead-range cache intercepts before heavy transforms
        out2, st2 = compress_terminal("build", raw)

        self.assertEqual(st2["gate"], "reverted_gate")
        self.assertTrue(st2.get("cached_dead_range", False))
        self.assertEqual(out1, out2)

    def test_compressible_output_not_cached_as_dead(self):
        _DEAD_RANGES.clear()
        # Highly repetitive compressible output
        raw = "repeated status line ok\n" * 200

        out, st = compress_terminal("test", raw)
        self.assertEqual(st["gate"], "ok")
        self.assertFalse(st.get("cached_dead_range", False))


if __name__ == "__main__":
    unittest.main()
