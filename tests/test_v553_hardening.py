"""Regression tests for the v5.5.3 hardening pass.

Each test pins one defect found by the bug hunt, so reverting the fix turns it
red instead of silently degrading compression quality or losing data.
"""
from trimtok.delta_engine import _normalize_command
from trimtok.kv_lock import stabilize_prompt
from trimtok.prerewrite import rewrite_argv, rewrite_command
from trimtok.pruners import collapse_success_output
from trimtok.signals import has_marker_head, is_important
from trimtok.strategies.lossless_min import minify_lossless


# --- 1. minify_lossless must not swallow the file after a stray "/*" --------
def test_minify_keeps_code_after_a_block_comment_opener_in_a_string():
    src = ['COMMENT_START = "/*"', "def strip(s):", "    return s", "Copyright 2024 Acme Corp."]
    src += [f"def fn{i}(x):" for i in range(60)]
    src += [f"    return x + {i}" for i in range(60)]
    text = "\n".join(f"{i + 1}|{line}" for i, line in enumerate(src))

    out, _ = minify_lossless(text)

    assert len(out) == len(text)
    assert "fn0" in out and "fn59" in out


def test_minify_still_strips_a_real_license_header():
    text = "\n".join([
        "1|/*",
        "2| * Copyright 2024 Acme Corp.",
        "3| * All rights reserved.",
        "4| */",
        "5|def real():",
        "6|    return 1",
    ])

    out, _ = minify_lossless(text)

    assert "Copyright" not in out
    assert "def real():" in out


# --- 2. decision_recoverable_rate lives in tests/test_decision_eval.py ------


# --- 3. kv_lock must survive a second pass without losing values ------------
def test_kv_lock_is_idempotent_and_loses_nothing():
    prompt = "Deployed 2026-11-06T10:00:00Z by ci\nSession 550e8400-e29b-41d4-a716-446655440000"
    once = stabilize_prompt(prompt)
    twice = stabilize_prompt(once)

    assert once == twice
    assert "2026-11-06T10:00:00Z" in twice
    assert "550e8400-e29b-41d4-a716-446655440000" in twice


def test_kv_lock_keeps_a_value_that_spans_lines():
    prompt = "Report generated May\n15, 2024 by the nightly job."
    once = stabilize_prompt(prompt)

    assert "15, 2024" in once


# --- 4. command rewriting must not re-split the caller's arguments ----------
def test_rewrite_argv_preserves_a_quoted_argument():
    argv = ["pytest", "-k", "not slow"]

    out = rewrite_argv(argv)

    assert out == ["pytest", "-k", "not slow", "-q", "--tb=short"]
    assert rewrite_command('pytest -k "not slow"') == 'pytest -k \'not slow\' -q --tb=short'


def test_rewrite_argv_leaves_other_commands_alone():
    assert rewrite_argv(["git", "status", "-s"]) == ["git", "status", "-s"]
    assert rewrite_argv(["pytest", "-v"]) == ["pytest", "-v"]


# --- 5. a failed install must never be collapsed into a success line --------
def test_failed_yarn_install_is_not_collapsed():
    log = "\n".join(
        ["yarn install v1.22.19", "info No lockfile found."]
        + [f"[{i}/4] Resolving packages... step {i}" for i in range(80)]
        + ['error An unexpected error occurred: "getaddrinfo ENOTFOUND".']
    )

    assert collapse_success_output("yarn install", log) == log


def test_successful_yarn_install_is_still_collapsed():
    log = "\n".join(
        ["yarn install v1.22.19"]
        + [f"[{i}/4] Resolving packages... step {i}" for i in range(80)]
        + ["Done in 12.34s."]
    )

    assert collapse_success_output("yarn install", log).startswith("[tt:success")


# --- 6. error polarity must cover the common phrasings ---------------------
def test_error_detection_covers_missing_files_and_request_log_5xx():
    assert is_important("cp: cannot stat 'src/app.conf': No such file or directory")
    assert is_important("GET /api/users/250 500 12ms")
    assert is_important('127.0.0.1 - - "GET /x HTTP/1.1" 500 12')
    assert is_important("ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND")


def test_error_detection_ignores_benign_lines():
    assert not is_important("GET /api/users/250 200 3ms")
    assert not is_important("compiled 42 modules in 1.2s")
    assert not is_important("All 12 tests passed")


# --- 7. cosmetic flags must not merge different command outputs ------------
def test_delta_keeps_output_changing_flags_in_the_snapshot_key():
    assert _normalize_command("git status -s") != _normalize_command("git status")
    assert _normalize_command("pytest -q") == _normalize_command("pytest")


# --- 8. the sentinel guard must only fire on trimtok's own output ----------
def test_marker_detection_requires_the_first_line():
    real = "[tt:terminal kind=text hidden=72 #c16l]\n[tt:legend] Fold markers: ..."
    doc = "Some docs.\n\nExample output:\n[tt:terminal kind=text hidden=72 #c16l]\n"

    assert has_marker_head(real)
    assert not has_marker_head(doc)


# --- 9. web intake must not advertise a ref that was never stored ----------
def test_web_intake_marker_carries_the_real_digest():
    import hashlib

    from trimtok.dedup import compress_tool_result

    doc = "\n".join(f"{i + 1}|Paragraph {i} " + "lorem ipsum dolor sit amet " * 6 for i in range(40))
    out, _stats = compress_tool_result("web_extract", doc, args={"url": "https://example.com"})

    assert "#doc" not in out
    assert f"#{hashlib.sha256(doc.encode()).hexdigest()[:12]}" in out
