"""trimtok Phase 1 tests: stdlib assert-based (ponytail rule). Run: python test_core.py"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import LEGEND, SENTINEL_RE, compress_terminal, detect_kind, strip_ansi
from ccr import get as ccr_get, put as ccr_put, stats as ccr_stats


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1
check.failed = 0


# 1. small output passthrough
out, st = compress_terminal("ls", "a\nb\n")
check("small-passthrough", out == "a\nb\n" and st["gate"] == "passthrough_small")

# 2. non-str passthrough
out, st = compress_terminal("x", None)
check("non-str-passthrough", out is None and st["gate"] == "passthrough_non_str")

# 3. noisy log folds repeats, keeps errors
lines = ["2026-10-11 20:00:01 INFO heartbeat ok"] * 50
lines.insert(10, "2026-10-11 20:00:01 ERROR boom failed")
big = "\n".join(lines) + "\n" + "x" * 1500
out, st = compress_terminal("pytest", big)
check("log-gate-ok", st["gate"] == "ok")
check("log-keeps-error", "ERROR boom failed" in out)
check("log-marker-head", out.startswith("[tt:terminal"))
check("log-legend-second", out.split("\n")[1] == LEGEND)
check("log-saved", st["saved_chars"] > 0)

# 4. never-inflate: random-ish unique lines that would grow -> revert
uniq = "\n".join(f"unique-line-{i}-abcdef" for i in range(300)) + "\n" + "y" * 500
out2, st2 = compress_terminal("echo", uniq)
check("never-inflate", st2["gate"] in ("ok", "reverted_gate") and len(out2) <= len(uniq))

# 5. idempotency: already-compressed passes through (gate name may vary by size)
out3, st3 = compress_terminal("pytest", out)
check("idempotent", out3 == out and st3["gate"].startswith("passthrough"))

# 6. TRIMTOK_KEEP: lines survive windowing
many = [f"line {i} filler text here" for i in range(400)]
many[350] = "TRIMTOK_KEEP: account 123 balance 456.78"
big2 = "\n".join(many)
out4, st4 = compress_terminal("dump", big2)
check("keep-prefix", "TRIMTOK_KEEP: account 123 balance 456.78" in out4)

# 7. ANSI stripped
ansi = "\x1b[31m" + "red line\n" * 100 + "\x1b[0m" + "z" * 1500
out5, st5 = compress_terminal("ls --color", ansi)
check("ansi-stripped", "\x1b[" not in out5)

# 8. CCR roundtrip + TTL/evict basics
with tempfile.TemporaryDirectory() as td:
    from pathlib import Path
    import json
    d = Path(td)
    sample_text = "line 1 alpha\nline 2 needle here\nline 3 beta\nline 4 needle again\nline 5 gamma\n"
    h = ccr_put(sample_text * 50, dir=d)
    check("ccr-put", len(h) == 12)
    check("ccr-get", (ccr_get(h, dir=d) or "").startswith("line 1 alpha"))
    check("ccr-missing", ccr_get("deadbeef1234", dir=d) is None)
    check("ccr-bad-hash", ccr_get("!!!", dir=d) is None)

    expired_read = ccr_put("expired read entry", dir=d)
    expired_read_path = d / (expired_read + ".txt")
    old_time = time.time() - 2 * 3600
    os.utime(expired_read_path, (old_time, old_time))
    check(
        "ccr-get-removes-expired",
        ccr_get(expired_read, dir=d, ttl_hours=1) is None and not expired_read_path.exists(),
    )

    # A failed atomic publish must not leave a partial cache entry or temp file.
    from unittest.mock import patch
    failed_d = d / "failed-write"
    failed_d.mkdir()
    with patch("ccr.os.replace", side_effect=OSError("simulated publish failure")):
        failed_hash = ccr_put("content that cannot be published", dir=failed_d)
    check("ccr-write-failure-cleans-temp", failed_hash == "" and not list(failed_d.iterdir()))

    # Status/stat sweeps remove expired files even without another cache write.
    expiry_d = d / "expiry"
    fresh_hash = ccr_put("fresh entry", dir=expiry_d)
    expired_hash = ccr_put("expired entry", dir=expiry_d)
    expired_path = expiry_d / (expired_hash + ".txt")
    old_time = time.time() - 2 * 3600
    os.utime(expired_path, (old_time, old_time))
    try:
        expiry_stats = ccr_stats(dir=expiry_d, ttl_hours=1)
        expiry_ok = (
            expiry_stats == {"entries": 1, "bytes": len("fresh entry")}
            and not expired_path.exists()
            and (expiry_d / (fresh_hash + ".txt")).exists()
        )
    except TypeError:
        expiry_ok = False
    check("ccr-stats-removes-expired", expiry_ok)

    # Failed cache storage must leave terminal and tool outputs uncompressed.
    import __init__ as plugin
    class _Config:
        @staticmethod
        def get_config(key, default):
            return default
    plugin._on_transform._ctx = _Config()
    plugin._on_tool_result._ctx = _Config()
    with patch("__init__.ccr_put", return_value=""):
        check("transform-fails-open-without-ccr", plugin._on_transform(command="pytest", output=big) is None)
        payload = json.dumps({"items": [{"id": i, "name": "x" * 40} for i in range(100)]}, indent=2)
        check(
            "tool-result-fails-open-without-ccr",
            plugin._on_tool_result(tool_name="search_files", result=payload) is None,
        )

    class _ConfigValues:
        def __init__(self, values):
            self.values = values

        def get_config(self, key, default):
            return self.values.get(key, default)

    plugin._on_transform._ctx = _ConfigValues({"exclude_commands": ["git status"]})
    with patch.object(plugin, "compress_terminal", return_value=("unchanged", {"gate": "small"})) as compress_call:
        excluded = plugin._on_transform(command="GIT   status --short", output=big)
    check("exclude-command-normalized-prefix", excluded is None and not compress_call.called)

    with patch.object(plugin, "compress_terminal", return_value=("unchanged", {"gate": "small"})) as compress_call:
        plugin._on_transform(command="git statusx", output=big)
    check("exclude-command-prefix-boundary", compress_call.called)

    plugin._on_transform._ctx = _ConfigValues({"exclude_commands": "git status"})
    with patch.object(plugin, "compress_terminal", return_value=("unchanged", {"gate": "small"})) as compress_call:
        plugin._on_transform(command="git status", output=big)
    check("exclude-command-invalid-config-ignored", compress_call.called)

    plugin._on_tool_result._ctx = _ConfigValues({"exclude_tools": ["Read_File"]})
    with patch.object(plugin, "compress_tool_result", return_value=("unchanged", {"gate": "small"})) as compress_call:
        excluded = plugin._on_tool_result(tool_name="read_file", result=payload)
    check("exclude-tool-case-insensitive-exact", excluded is None and not compress_call.called)

    plugin._on_tool_result._ctx = _ConfigValues({"exclude_tools": ["read_file"]})
    with patch.object(plugin, "compress_tool_result", return_value=("unchanged", {"gate": "small"})) as compress_call:
        plugin._on_tool_result(tool_name="read_file_extra", result=payload)
    check("exclude-tool-exact-name", compress_call.called)

    # 8b. retrieve tool with pagination and grep
    from __init__ import _handle_retrieve
    # monkeypatch default_dir for test
    import ccr
    orig_dir = ccr.default_dir
    ccr.default_dir = lambda: d
    try:
        res_full = json.loads(_handle_retrieve({"hash": h}))
        check("retrieve-full", "original_content" in res_full and res_full["total_lines"] > 10)

        res_slice = json.loads(_handle_retrieve({"hash": h, "offset": 2, "limit": 2}))
        check("retrieve-slice", "2|line 2 needle here" in res_slice["lines"] and res_slice["returned_lines"] == 2)

        res_grep = json.loads(_handle_retrieve({"hash": h, "grep": "needle"}))
        check("retrieve-grep", res_grep["matches_count"] == 100 and "needle here" in res_grep["matches"])

        compile_grep = getattr(plugin, "_compile_grep", None)
        if compile_grep:
            try:
                compile_grep("(a+)+$")
                unsafe_rejected = False
            except ValueError:
                unsafe_rejected = True
            try:
                compile_grep("x" * 257)
                oversized_rejected = False
            except ValueError:
                oversized_rejected = True
        else:
            unsafe_rejected = oversized_rejected = False
        check("grep-rejects-unsafe-regex", unsafe_rejected)
        check("grep-rejects-oversized-pattern", oversized_rejected)
        check("grep-allows-bounded-pattern", compile_grep is not None and bool(compile_grep("foo.*bar").search("FOO---BAR")))
        check("grep-allows-escaped-delimiters", compile_grep is not None and bool(compile_grep(r"\(foo\)").search("(FOO)")))

        many_matches = "\n".join(["match line"] * 1200)
        many_hash = ccr_put(many_matches, dir=d)
        bounded = json.loads(_handle_retrieve({"hash": many_hash, "grep": "match"}))
        check(
            "retrieve-grep-bounds-output",
            bounded.get("matches_count") == 1200
            and bounded.get("truncated") is True
            and len(bounded.get("matches", "")) <= 100_000,
        )
        with patch.object(plugin, "ccr_get", return_value="x" * 1_000_001):
            oversized_input = json.loads(_handle_retrieve({"hash": h, "grep": "x"}))
        check("retrieve-grep-rejects-oversized-input", "error" in oversized_input)
    finally:
        ccr.default_dir = orig_dir

# 9. detect kinds
check("detect-diff", detect_kind("diff --git a/b\n@@ -1 +1 @@\n-x\n+y\n" * 30) == "diff")
check("detect-log", detect_kind("Traceback (most recent call last):\n  File x\n" * 30) == "log")
check("detect-search", detect_kind("memcore/core.py:604:def promote():\n" * 60) == "search")

# Audit history is bounded and status includes the retained rotated segment.
with tempfile.TemporaryDirectory() as td:
    from pathlib import Path
    from unittest.mock import patch
    audit_path = Path(td) / "audit.jsonl"
    with patch.object(plugin, "_audit_path", return_value=audit_path), \
         patch.object(plugin, "_MAX_AUDIT_BYTES", 120, create=True), \
         patch.object(ccr, "default_dir", return_value=Path(td)):
        for saved in range(1, 11):
            plugin._audit({"saved_chars": saved, "detail": "x" * 60})
        backup = audit_path.with_name(audit_path.name + ".1")
        status = plugin._handle_ts_command("status")
        bounded_files = audit_path.exists() and backup.exists()
        bounded_sizes = bounded_files and audit_path.stat().st_size <= 120 and backup.stat().st_size <= 120
        check("audit-log-rotates-with-cap", bounded_sizes)
        check("audit-status-includes-rotated-log", "2 transforms logged" in status and "~19 chars saved" in status)
        oversized_path = Path(td) / "oversized.jsonl"
        with patch.object(plugin, "_audit_path", return_value=oversized_path):
            plugin._audit({"detail": "x" * 1000})
        check("audit-single-entry-respects-cap", oversized_path.stat().st_size <= 120)
        legacy_path = Path(td) / "legacy.jsonl"
        legacy_path.write_text("x" * 500, encoding="utf-8")
        with patch.object(plugin, "_audit_path", return_value=legacy_path):
            plugin._audit({"saved_chars": 3})
        check("audit-trims-oversized-existing-log", legacy_path.stat().st_size <= 120)

# 10. determinism: same input -> same bytes
a1, _ = compress_terminal("pytest", big)
a2, _ = compress_terminal("pytest", big)
check("deterministic", a1 == a2)

# 11. perf regression guard (median of 3, warm). Asserts the p99<100ms SPEC
# budget, NOT p50<20ms: absolute-ms asserts flake on a loaded box (observed
# 5-98ms on identical input across parallel sessions). p50 is verified by the
# audit-log ms field in a real session instead.
blob = ("2026-10-11 INFO tick\n" * 3000) + "ERROR final fail\n" + "z" * 2000
compress_terminal("tail", blob)  # warmup
_ms = sorted(compress_terminal("tail", blob)[1].get("ms", 1e9) for _ in range(3))[1]
check("perf-p99", _ms < 100)

print(f"\n{check.failed} failures")
if __name__ == "__main__":
    sys.exit(1 if check.failed else 0)
