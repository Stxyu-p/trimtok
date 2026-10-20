"""Regression tests for the 5 blockers Argon 4 found in the v4.2 review.

Each test reproduces a concrete failure from TRIMTOK_V4_ARGON_REVIEW.md, so the
bug cannot come back silently. Four of them (the two syntax-corruption cases, the
oversize re-read case and the unified-diff case) were verified to FAIL against the
pre-fix code and to PASS after it; the rest pin the contract that must keep
holding:

  BLOCKER-1  smart_skeletonize_code produced IndentationError / SyntaxError
  BLOCKER-2  a re-read after a cold skeleton claimed "identical to prior read"
  BLOCKER-3  hygiene() corrupted a unified diff in the 512-2047 char band
  BLOCKER-4  adaptive_tool_head was not wired into compress_tool_result
  BLOCKER-5  invalidate_shell_mutations was never called
"""

from __future__ import annotations

import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trimtok.core import compress_terminal
from trimtok.dedup import clear_session, compress_tool_result
from trimtok.pruners import smart_skeletonize_code


# --------------------------------------------------------------------------
# BLOCKER-1: the skeletonizer must emit valid Python
# --------------------------------------------------------------------------
CLASS_WITH_DOCSTRING = '''\
import os


class Service:
    """Service docs.

    A second paragraph, so the docstring spans several lines.
    """

    # The blank line right below the docstring is the trap: the fold used to read
    # its indentation from that line and emit `pass` at the wrong depth.
    def compute(self, value: int) -> int:
        """Compute the thing."""

        total = value
        for index in range(10):
            total += index
            total += index * 2
            total += index * 3
            total += index * 4
            total += index * 5
            total += index * 6
            total += index * 7
            total += index * 8
            total += index * 9
            total += index * 10
            total += index * 11
            total += index * 12
            total += index * 13
            total += index * 14
        return total

    def other(self, value: int) -> int:
        total = value
        for index in range(10):
            total += index * 2
            total += index * 3
            total += index * 4
            total += index * 5
            total += index * 6
            total += index * 7
            total += index * 8
            total += index * 9
            total += index * 10
            total += index * 11
            total += index * 12
            total += index * 13
            total += index * 14
        return total
'''


NESTED_DECORATED = '''\
import functools


def outer(value: int) -> int:
    @functools.cache
    def inner(item: int) -> int:
        total = item
        for index in range(10):
            total += index
            total += index * 2
            total += index * 3
            total += index * 4
            total += index * 5
            total += index * 6
            total += index * 7
            total += index * 8
            total += index * 9
            total += index * 10
            total += index * 11
            total += index * 12
            total += index * 13
            total += index * 14
        return total

    return inner(value)
'''


def test_skeleton_of_class_method_with_docstring_is_valid_python():
    """A method whose docstring is followed by a blank line must not dedent."""
    skeleton, saved, count = smart_skeletonize_code(CLASS_WITH_DOCSTRING, ref="#t1")
    assert count >= 1, "expected at least one folded method"
    assert saved > 0
    ast.parse(skeleton)  # raises IndentationError on the old code


def test_skeleton_of_nested_decorated_def_is_valid_python():
    """A @decorator above a folded nested def must be folded with it."""
    skeleton, saved, count = smart_skeletonize_code(NESTED_DECORATED, ref="#t2")
    assert count >= 1, "expected the nested def to be folded"
    assert saved > 0
    ast.parse(skeleton)  # raises SyntaxError on the old code


# --------------------------------------------------------------------------
# BLOCKER-2: no lying about what the agent has already seen
# --------------------------------------------------------------------------
BIG_MODULE = "import os\n\n\n" + "\n".join(
    f"def fn_{i}(value: int) -> int:\n"
    f'    """Doc {i}."""\n'
    f"    total = value\n"
    f"    for index in range(12):\n"
    + "".join(f"        total += index * {k}\n" for k in range(2, 19))
    + "    return total\n"
    for i in range(30)
)


def test_oversize_reread_after_cold_skeleton_returns_the_real_file():
    """A cold skeleton must not make the next read claim "identical"."""
    clear_session("argon_b2")
    body = BIG_MODULE
    out1, st1 = compress_tool_result(
        "read_file", body, args={"path": "src/big.py"}, session_id="argon_b2")
    assert st1["gate"] == "ok", st1
    assert st1["kind"] == "file_skeleton", st1

    out2, st2 = compress_tool_result(
        "read_file", body, args={"path": "src/big.py"}, session_id="argon_b2")
    # The agent only ever saw a skeleton, so it must get the real body back --
    # never an outline that says "identical to prior read in active context".
    assert "file_unchanged" not in out2, out2[:400]
    assert st2.get("kind") != "file_cache_hit", st2
    assert out2 == body, "the agent asked for the file; give it the file"


def test_file_cache_hit_still_applies_after_a_full_read():
    """A full (small) read may be served as an outline on the next read."""
    clear_session("argon_b2b")
    body = "import os\n\n\n" + "\n".join(
        f"def fn_{i}(value: int) -> int:\n    total = value\n    return total + {i}\n"
        for i in range(60))
    out1, st1 = compress_tool_result(
        "read_file", body, args={"path": "src/small.py"}, session_id="argon_b2b")
    assert st1["gate"] == "passthrough_reference", st1
    out2, st2 = compress_tool_result(
        "read_file", body, args={"path": "src/small.py"}, session_id="argon_b2b")
    assert st2.get("kind") == "file_cache_hit", st2
    assert "file_unchanged" in out2


# --------------------------------------------------------------------------
# BLOCKER-3: a unified diff must survive the small band untouched
# --------------------------------------------------------------------------
GIT_DIFF = (
    "diff --git a/src/app.py b/src/app.py\n"
    "index 1234567..89abcde 100644\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -10,7 +10,7 @@ def handler():\n"
    "     ctx = build()\n"
    " \n"
    "     if not ctx:\n"
    "         return None\n"
    " \n"
    "     if ctx.empty:\n"
    "         return None\n"
    " \n"
    "     if ctx.stale:\n"
    "         return None\n"
    " \n"
    "     if ctx.expired:\n"
    "         return None\n"
    " \n"
    "     return ctx.render()\n"
) + ("    # padding line to push it past the 512 char floor\n" * 8)


def test_unified_diff_context_lines_keep_their_leading_space():
    """hygiene() would strip the single space on blank diff context lines."""
    assert 512 <= len(GIT_DIFF) < 2048, len(GIT_DIFF)
    clear_session("argon_b3")
    out, st = compress_tool_result("terminal", GIT_DIFF, session_id="argon_b3")
    assert "\n \n" in out or out.count("\n \n") >= 1, "diff context lines lost their space"
    assert "@@ -10,7 +10,7 @@" in out
    assert "     if not ctx:" in out


def test_diff_in_the_small_terminal_band_is_not_corrupted():
    """The 512-2047 char terminal band must not rewrite a diff."""
    out, st = compress_terminal("git diff", GIT_DIFF, min_chars=2048)
    assert "@@ -10,7 +10,7 @@" in out
    assert " \n" in out or " \n" in out.replace("\r", "")


# --------------------------------------------------------------------------
# BLOCKER-4: the adaptive tool header must be wired in
# --------------------------------------------------------------------------
def test_compress_tool_result_uses_the_adaptive_header():
    """A big fold must carry a scaled header, not the fixed v4.1 header."""
    clear_session("argon_b4")
    payload = json.dumps({"items": [{"id": i, "n": f"x_{i}"} for i in range(60)]}, indent=2)
    out, st = compress_tool_result("search_files", payload, session_id="argon_b4")
    assert st["gate"] == "ok", st
    assert st["saved_chars"] > 0
    assert out.startswith("[tt:"), out[:120]
    # The token gate: the shipped result must not be bigger in tokens.
    assert st["est_tokens_after"] <= st["est_tokens_before"], st


# --------------------------------------------------------------------------
# BLOCKER-5: shell mutations must invalidate the cached file body
# --------------------------------------------------------------------------
def test_shell_mutation_invalidates_the_cached_file():
    """`git checkout -- file.py` must drop the cached body for that path."""
    clear_session("argon_b5")
    body = "import os\n\n\n" + "\n".join(
        f"def fn_{i}(value: int) -> int:\n    return value + {i}\n" for i in range(30))
    compress_tool_result("read_file", body, args={"path": "src/x.py"}, session_id="argon_b5")
    # A shell command that rewrites the file invalidates the cache entry.
    compress_terminal(
        "git checkout -- src/x.py", "restored\n", session_id="argon_b5")
    import os as _os
    from trimtok.dedup import _seen
    cache = _seen("argon_b5").get("files", {})
    key = _os.path.normcase(_os.path.normpath("src/x.py"))
    assert key not in cache, f"cache still holds the stale body: {list(cache)}"
