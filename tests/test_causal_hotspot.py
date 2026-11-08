"""Unit and integration tests for Causal Hotspots (traceback error to read_file focus)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-hotspot-"))

from trimtok.signals import extract_traceback_hotspots


class TestTracebackHotspotExtraction(unittest.TestCase):
    def test_extract_python_traceback(self):
        tb = (
            'Traceback (most recent call last):\n'
            '  File "trimtok/core.py", line 540, in compress_terminal\n'
            '    out = _classify(lines)\n'
            '  File "trimtok/signals.py", line 120, in _classify\n'
            '    assert False, "boom"\n'
            'AssertionError: boom\n'
        )
        spots = extract_traceback_hotspots(tb)
        norm_core = os.path.normcase(os.path.normpath("trimtok/core.py"))
        norm_sig = os.path.normcase(os.path.normpath("trimtok/signals.py"))
        self.assertIn(norm_core, spots)
        self.assertIn(norm_sig, spots)
        self.assertEqual(spots[norm_core], [540])
        self.assertEqual(spots[norm_sig], [120])

    def test_extract_pytest_failure_line(self):
        output = (
            "tests/test_diff_lane.py:28: in test_diff_lane\n"
            "    assert line in out, f'changed line dropped: {line}'\n"
            "E   AssertionError: changed line dropped: +foo\n"
        )
        spots = extract_traceback_hotspots(output)
        norm_diff = os.path.normcase(os.path.normpath("tests/test_diff_lane.py"))
        self.assertIn(norm_diff, spots)
        self.assertEqual(spots[norm_diff], [28])

    def test_extract_compiler_error(self):
        output = (
            "src/main.rs:42:15: error: cannot borrow immutable local variable\n"
            "src/engine.ts:105:7 - error TS2322: Type 'string' is not assignable\n"
        )
        spots = extract_traceback_hotspots(output)
        norm_rs = os.path.normcase(os.path.normpath("src/main.rs"))
        norm_ts = os.path.normcase(os.path.normpath("src/engine.ts"))
        self.assertIn(norm_rs, spots)
        self.assertEqual(spots[norm_rs], [42])
        self.assertIn(norm_ts, spots)
        self.assertEqual(spots[norm_ts], [105])

    def test_ignore_non_source_extensions(self):
        output = (
            "error reading config.json:10: invalid character\n"
            "build.log:200: build finished\n"
        )
        spots = extract_traceback_hotspots(output)
        self.assertEqual(len(spots), 0)


if __name__ == "__main__":
    unittest.main()
