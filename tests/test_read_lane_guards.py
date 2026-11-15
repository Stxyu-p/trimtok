"""Regression guards for the read-file skeleton lane (5.9.4).

Three ways a cold large read used to ship 0% saved with a reason that hid the
cause: a hotspot range that spans the file (focused skeleton preserves every
body it touches), a payload whose pre-skeleton passes eat the guard budget, and
a file with nothing to fold. It now falls back to a plain skeleton, and
a file that cannot fold names itself in `reason` instead of looking like a
size cutoff.
"""

import json
import os
import unittest

import trimtok.dedup as dedup
from trimtok.dedup import compress_tool_result, clear_session


def _payload(path, body):
    lines = body.split("\n")
    numbered = "\n".join(f"{i + 1}|{line}" for i, line in enumerate(lines))
    return json.dumps({"content": numbered, "total_lines": len(lines)})


def _long_fn_source(fn_count, body_lines):
    parts = []
    for i in range(fn_count):
        body = "\n".join(f"    x{j} = {j} * a" for j in range(body_lines))
        parts.append(f"def f{i}(a):\n{body}\n    return x0")
    return "\n\n".join(parts)


class FocusRetryTests(unittest.TestCase):
    def setUp(self):
        clear_session("guard_focus")

    def test_wide_hotspot_still_compresses(self):
        src = _long_fn_source(40, 25)
        path = os.path.normcase(os.path.normpath("/proj/src/wide.py"))
        # Every line is a hotspot: the focused skeleton can fold nothing.
        dedup._seen("guard_focus")["hotspots"][path] = list(range(1, len(src.split("\n")) + 1))
        out, stats = compress_tool_result(
            "read_file", _payload("/proj/src/wide.py", src),
            args={"path": "/proj/src/wide.py", "offset": 1, "limit": 2000},
            session_id="guard_focus", min_chars=2048,
        )
        self.assertEqual(stats.get("gate"), "ok")
        self.assertEqual(stats.get("kind"), "file_skeleton")
        self.assertLess(len(out), len(src))


class BailReasonTests(unittest.TestCase):
    def setUp(self):
        clear_session("guard_reason")

    def test_unfoldable_file_names_the_cause(self):
        # One-line functions: the skeleton has nothing to fold, and the reason
        # must say so instead of looking like a size cutoff.
        src = "\n".join(f"def f{i}(): pass" for i in range(600))
        out, stats = compress_tool_result(
            "read_file", _payload("/proj/src/flat.py", src),
            args={"path": "/proj/src/flat.py", "offset": 1, "limit": 2000},
            session_id="guard_reason", min_chars=2048,
        )
        self.assertEqual(stats.get("gate"), "passthrough_reference")
        self.assertEqual(stats.get("reason"), "skeleton_no_gain")


if __name__ == "__main__":
    unittest.main()
