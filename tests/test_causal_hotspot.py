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


class TestAstFocusProtection(unittest.TestCase):
    def test_smart_skeletonize_protects_hotspot_function(self):
        from trimtok.pruners import smart_skeletonize_code

        code_lines = [
            "def helper_func(x):",
        ] + [f"    a_{i} = {i}" for i in range(25)] + [
            "    return a_0",
            "",
            "def target_func(y):",
        ] + [f"    b_{i} = {i}" for i in range(25)] + [
            "    return b_0",
        ]
        code = "\n".join(code_lines)
        line_prefixes = [f"{i+1}|" for i in range(len(code_lines))]
        line_nos = list(range(1, len(code_lines) + 1))

        # Without focus_lines: both functions should be folded
        res_default, _, count = smart_skeletonize_code(
            code, line_prefixes=line_prefixes, line_nos=line_nos
        )
        self.assertEqual(count, 2)
        self.assertIn("[tt:body", res_default)

        # Target line inside target_func (approx line 32)
        target_line = 32
        res_focused, _, count_focused = smart_skeletonize_code(
            code, line_prefixes=line_prefixes, line_nos=line_nos, focus_lines=[target_line]
        )
        self.assertEqual(count_focused, 1)  # only helper_func folded!
        self.assertIn("def helper_func", res_focused)
        self.assertIn("def target_func", res_focused)
        # helper_func was folded:
        self.assertIn("[tt:body L2-", res_focused)
        # target_func was NOT folded:
        self.assertIn("35|    b_5 = 5", res_focused)

    def test_ts_skeleton_protects_hotspot_function(self):
        from trimtok.ts_skeleton import skeletonize_code_full, _get_ts_lang

        # Test python dispatch through skeletonize_code_full
        py_lines = [
            "def helperFunc():",
        ] + [f"    a_{i} = 'some_long_padding_string_{i}'" for i in range(25)] + [
            "    return 0",
            "",
            "def targetFunc():",
        ] + [f"    b_{i} = 'some_long_padding_string_{i}'" for i in range(25)] + [
            "    return 1",
        ]
        py_code = "\n".join(py_lines)
        pfx = [f"{i+1}|" for i in range(len(py_lines))]
        nos = list(range(1, len(py_lines) + 1))
        res_py, _, count_py = skeletonize_code_full(
            py_code, filename="index.py", line_prefixes=pfx, line_nos=nos,
            focus_lines=[32]
        )
        self.assertEqual(count_py, 1)
        self.assertIn("35|    b_5 = ", res_py)

        # Test tree-sitter JS if available
        if _get_ts_lang("javascript") is None:
            return

        code_lines = [
            "function helperFunc() {",
        ] + [f"    const a_{i} = 'some_long_padding_string_{i}';" for i in range(25)] + [
            "    return 0;",
            "}",
            "",
            "function targetFunc() {",
        ] + [f"    const b_{i} = 'some_long_padding_string_{i}';" for i in range(25)] + [
            "    return 1;",
            "}",
        ]
        code = "\n".join(code_lines)
        line_prefixes = [f"{i+1}|" for i in range(len(code_lines))]
        line_nos = list(range(1, len(code_lines) + 1))

        res_focused, _, count_focused = skeletonize_code_full(
            code, filename="index.js", line_prefixes=line_prefixes, line_nos=line_nos,
            focus_lines=[32]
        )
        self.assertEqual(count_focused, 1)
        self.assertIn("35|    const b_5 = ", res_focused)


if __name__ == "__main__":
    unittest.main()
