"""The diff lane must never drop a changed line, only context."""
import os
import tempfile

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-difflane-"))

from trimtok.core import compress_terminal


def _big_diff(n_changes: int, context: int) -> str:
    out = ["diff --git a/x.py b/x.py", "--- a/x.py", "+++ b/x.py", "@@ -1,999 +1,999 @@"]
    for i in range(n_changes):
        out.extend([" " + f"ctx {i} {j}" for j in range(context)])
        out.append(f"-old line {i}")
        out.append(f"+new line {i}")
    return "\n".join(out)


def test_diff_lane_keeps_every_changed_line_past_the_window_budget():
    # 400 changed lines is double the 200-line terminal budget: the old window
    # sampled them and shipped a diff missing 256 of 453 lines on a real patch.
    src = _big_diff(200, 6)
    out, st = compress_terminal("git diff", src)
    changed = [ln for ln in src.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    assert st["gate"] == "ok"
    assert len(changed) == 400
    for line in changed:
        assert line in out, f"changed line dropped: {line}"
    assert len(out) < len(src)


def test_diff_lane_still_compresses_when_context_dominates():
    src = _big_diff(20, 30)
    out, st = compress_terminal("git diff", src)
    assert st["gate"] == "ok"
    assert len(out) < len(src) * 0.6
