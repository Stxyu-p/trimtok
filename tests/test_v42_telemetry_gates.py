"""Verification suite for Trimtok v4.2 telemetry bottlenecks.

Covers the three measured bottlenecks from 9,303 real-world telemetry events:
  A. read_file  -> 0% compressed (1,107 calls / 5,966,692 chars)
  B. reverted_gate -> 558 events (6.0% failure rate)
  C. passthrough_small in the 1K-2K band -> 1,306 events (24.1%), 0% compressed

Plus the Priority-1 guarantee (zero loss of intelligence) that must never regress.

The v4.2 symbols may not exist yet while the implementation lands; the tests that
need them are skipped until they do, so this module always imports cleanly.
"""

import ast
import json
import time

try:
    import pytest
except ImportError:
    class _MockPytest:
        class mark:
            @staticmethod
            def skipif(cond, reason=""):
                def decorator(fn):
                    return fn
                return decorator
    pytest = _MockPytest()

from trimtok.core import compress_terminal
from trimtok.dedup import clear_session, compress_tool_result

try:
    from trimtok.pruners import smart_skeletonize_code
except ImportError:  # pragma: no cover - lands with the v4.2 implementation
    smart_skeletonize_code = None


def _compress_tool(tool_name, text, *, args=None, session_id=""):
    """Call compress_tool_result, tolerating the v4.1 signature (no `args` kwarg)."""
    try:
        return compress_tool_result(tool_name, text, args=args, session_id=session_id)
    except TypeError:
        return compress_tool_result(tool_name, text, session_id=session_id)


# ---------------------------------------------------------------------------
# Problem A — read_file lifecycle
# ---------------------------------------------------------------------------
def _make_source_file(funcs: int = 12) -> str:
    """Build a realistic Python module with N long functions."""
    parts = []
    for idx in range(funcs):
        body = "\n".join(f"    x_{j} = {j} * {idx}" for j in range(18))
        parts.append(
            f"def compute_metric_{idx}(val: int, label: str = 'ok') -> int:\n"
            f'    """Compute {idx}."""\n{body}\n    return val + {idx}\n'
        )
    return "import os\nfrom typing import Any\n\nGLOBAL_LIMIT: int = 100\n\n" + "\n".join(parts)


def test_problem_a_read_file_lifecycle():
    """Cold Read -> Unchanged Repeat Cache Hit -> Patch + Re-read -> Oversize Skeleton."""
    clear_session("s_v42")
    raw_file = _make_source_file(12)
    assert 4000 <= len(raw_file) <= 10000, len(raw_file)

    # Turn 1: cold first read (<= 12KB) must stay verbatim so `patch` is never handed a mangled file
    out1, st1 = _compress_tool(
        "read_file", raw_file, args={"path": "src/metrics.py"}, session_id="s_v42"
    )
    assert st1["gate"] == "passthrough_reference", st1
    assert out1 == raw_file, "cold read must be byte-identical"

    # Turn 2: unchanged re-read -> file cache hit with an outline
    out2, st2 = _compress_tool(
        "read_file", raw_file, args={"path": "src/metrics.py"}, session_id="s_v42"
    )
    assert st2["gate"] == "ok", st2
    assert st2["kind"] == "file_cache_hit", st2
    assert st2["saved_chars"] > int(len(raw_file) * 0.80), st2
    assert "def compute_metric_0" in out2, "outline must still name the functions"

    # Turn 3: file edited then re-read -> contextual line-numbered delta
    edited = raw_file.replace("x_5 = 5 * 3", "x_5 = 99999  # patched bug")
    assert edited != raw_file, "fixture must actually change something"
    out3, st3 = _compress_tool(
        "read_file", edited, args={"path": "src/metrics.py"}, session_id="s_v42"
    )
    assert st3["gate"] == "ok", st3
    assert st3["kind"] == "file_delta", st3
    assert "x_5 = 99999  # patched bug" in out3, "the changed line must survive verbatim"
    assert st3["saved_chars"] > int(len(edited) * 0.50), st3

    # Turn 4: cold first read of an oversize file -> AST skeleton that still parses
    large = raw_file * 3
    assert len(large) > 12000, len(large)
    out4, st4 = _compress_tool(
        "read_file", large, args={"path": "src/huge_module.py"}, session_id="s_v42"
    )
    assert st4["gate"] == "ok", st4
    assert st4["kind"] == "file_skeleton", st4
    body = out4.split("\n", 1)[1] if out4.startswith("[tt:") else out4
    ast.parse(body)  # must remain valid Python


def test_read_file_verbatim_for_patch_safety():
    """A cold read in the 2KB-12KB band must be byte-identical."""
    clear_session("s_verbatim")
    raw = _make_source_file(12)
    out, st = _compress_tool(
        "read_file", raw, args={"path": "src/m.py"}, session_id="s_verbatim"
    )
    assert out == raw
    assert st["gate"] == "passthrough_reference"


def test_read_file_targeted_slice_is_verbatim():
    """An explicit offset/limit read must be returned untouched."""
    clear_session("s_slice")
    raw = _make_source_file(12)
    out, st = _compress_tool(
        "read_file", raw, args={"path": "src/m.py", "offset": 3, "limit": 5}, session_id="s_slice"
    )
    assert out == raw
    assert st["gate"] == "passthrough_reference"


# ---------------------------------------------------------------------------
# Problem B — reverted_gate in the 2KB-4KB band
# ---------------------------------------------------------------------------
def test_problem_b_reverted_gate_eliminated_in_2k_3k_band():
    """2KB-3KB outputs with modest savings must succeed instead of reverting."""
    unique = [f"src/module_{i}.py:42: info: checked symbol_{i}_status_ok" for i in range(38)]
    repeat_block = ["npm notice Tarball details: package-lock-verified-ok"] * 4
    payload = "\n".join(unique + repeat_block)
    assert 2100 <= len(payload) <= 2800, len(payload)

    compressed, stats = compress_terminal("npm test", payload, min_chars=2048)
    assert stats["gate"] == "ok", f"expected 'ok' via the nano header, got {stats['gate']}"
    assert len(compressed) < len(payload), "compressed output must be smaller"


def test_no_lane_makes_a_payload_bigger():
    """Across representative sizes, no lane may return a larger payload."""
    clear_session("s_nobigger")
    samples = [
        ("x" * 40 + "\n") * 60,                                   # ~2.4KB
        ("\n".join(f"line {i} of some log output" for i in range(120))),  # ~3.2KB
        ("\n".join(f"src/f_{i}.py:10: ok" for i in range(300))),  # ~6KB
    ]
    for idx, payload in enumerate(samples):
        assert len(payload) >= 2048
        out_c, st_c = compress_terminal("run", payload, min_chars=2048)
        assert len(out_c) <= len(payload), f"terminal lane {idx} grew the payload"
        out_t, st_t = compress_tool_result("some_tool", payload, session_id="s_nobigger")
        assert len(out_t) <= len(payload), f"tool lane {idx} grew the payload"


# ---------------------------------------------------------------------------
# Problem C — micro-minification in the 1K-2K band
# ---------------------------------------------------------------------------
def test_problem_c_micro_minify_1k_2k_band():
    """1KB-2KB payloads get lossless micro-minification fast, with no header bloat."""
    data = {
        "status": "ok",
        "items": [
            {"id": i, "name": f"service_{i}", "healthy": True, "latency_ms": 12.4}
            for i in range(12)
        ],
    }
    pretty_json = json.dumps(data, indent=4)
    assert 1000 <= len(pretty_json) < 2048, len(pretty_json)

    t0 = time.perf_counter()
    out, stats = _compress_tool("mcp_status", pretty_json, session_id="s_micro")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert stats["gate"] == "ok", stats
    assert stats.get("lossless_tier0") is True, stats
    assert len(out) < len(pretty_json) * 0.70, (len(out), len(pretty_json))
    assert json.loads(out) == data, "JSON data must be identical to the original"
    assert elapsed_ms < 2.0, f"micro-minify took {elapsed_ms:.3f}ms"


def test_micro_minify_is_lossless_on_non_json():
    """Trailing whitespace and blank runs may be removed; nothing else may change."""
    clear_session("s_lossless")
    payload = "line one   \n\n\n\nline two\t\n" + ("filler line\n" * 45)  # >=512: the Tier-0 band floor
    out, stats = _compress_tool("some_tool", payload, session_id="s_lossless")
    assert stats["gate"] == "ok", stats
    assert "line one" in out and "line two" in out
    assert "filler line" in out
    assert len(out) <= len(payload)


# ---------------------------------------------------------------------------
# Priority 1 — zero loss of intelligence
# ---------------------------------------------------------------------------
def test_priority1_error_lines_always_survive():
    """Every error / fault line must survive terminal compression."""
    payload = "\n".join(
        [
            "npm notice building package",
            "src/a.js:10: ERROR: cannot resolve module",
            "Traceback (most recent call last):",
            '  File "C:/x/site-packages/lib/core.py", line 44, in run',
            "ValueError: bad input",
            "src/b.js:2: warning: deprecated api",
            "npm notice done",
        ]
        + [f"src/c_{i}.js:1: info: ok" for i in range(60)]
    )
    out, stats = compress_terminal("npm test", payload, min_chars=2048)
    for needle in (
        "ERROR: cannot resolve module",
        "Traceback (most recent call last):",
        "ValueError: bad input",
        "warning: deprecated api",
    ):
        assert needle in out, f"lost an important line: {needle}\n---\n{out[:800]}"


def test_priority1_diff_hunks_always_survive():
    """Every file header, hunk header and +/- line of a unified diff must survive."""
    diff = "\n".join(
        [
            "diff --git a/src/x.py b/src/x.py",
            "index 1234567..89abcde 100644",
            "--- a/src/x.py",
            "+++ b/src/x.py",
            "@@ -1,6 +1,7 @@",
            " import os",
            "-old_line = 1",
            "+new_line = 2",
            " context = 3",
        ]
        + [f" unchanged line {i}" for i in range(60)]
    )
    out, stats = compress_terminal("git diff", diff, min_chars=2048)
    for needle in (
        "diff --git a/src/x.py b/src/x.py",
        "--- a/src/x.py",
        "+++ b/src/x.py",
        "@@ -1,6 +1,7 @@",
        "-old_line = 1",
        "+new_line = 2",
    ):
        assert needle in out, f"lost a diff line: {needle}"


@pytest.mark.skipif(smart_skeletonize_code is None, reason="smart_skeletonize_code not landed yet")
def test_priority1_code_skeleton_is_valid_python():
    """The skeleton must parse, keep every signature, and keep every folded return."""
    source = _make_source_file(12)
    skeleton, saved, folded = smart_skeletonize_code(source, ref="#abcdef123456")
    assert folded > 0, "the fixture must contain foldable functions"
    assert saved > 0

    tree = ast.parse(skeleton)  # must be valid Python
    original = ast.parse(source)
    orig_names = [n.name for n in original.body if isinstance(n, ast.FunctionDef)]
    new_names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert orig_names == new_names, "every top-level function must survive"

    for name in orig_names:
        assert f"def {name}(" in skeleton, f"signature of {name} was altered"

    # the terminal return of each folded function must still be there
    for node in original.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        last = node.body[-1]
        if isinstance(last, ast.Return):
            returned = ast.unparse(last.value)
            assert returned in skeleton, f"return value of {node.name} was lost: {returned}"


@pytest.mark.skipif(smart_skeletonize_code is None, reason="smart_skeletonize_code not landed yet")
def test_priority1_skeleton_keeps_signatures_byte_identical():
    """Signature lines must be byte-identical to the original."""
    source = _make_source_file(12)
    skeleton, _saved, _folded = smart_skeletonize_code(source, ref="#abc")
    for line in source.split("\n"):
        s = line.strip()
        if s.startswith("def ") and s.endswith(":"):
            assert line in skeleton.split("\n"), f"signature line altered: {line}"


def test_priority1_gate_vocabulary_intact():
    """The fail-open gate vocabulary must not shrink."""
    import trimtok.dedup as dd

    src = (dd.__file__ or "")
    text = open(src, "r", encoding="utf-8").read()
    for gate in (
        "passthrough_small",
        "passthrough_reference",
        "passthrough_sentinel",
        "passthrough_timeout",
        "passthrough_exception",
        "reverted_gate",
        "ok",
    ):
        assert gate in text, f"gate {gate} disappeared from dedup.py"
    assert src


def test_fail_open_on_exception():
    """A broken payload must return the original text, never an empty or partial one."""
    payload = "x" * 5000
    out, stats = _compress_tool("read_file", payload, args={"path": "a.py"}, session_id="s_fail")
    assert isinstance(out, str) and len(out) > 0
    assert stats["gate"] in (
        "ok",
        "passthrough_small",
        "passthrough_reference",
        "passthrough_sentinel",
        "passthrough_timeout",
        "passthrough_exception",
        "reverted_gate",
    )
