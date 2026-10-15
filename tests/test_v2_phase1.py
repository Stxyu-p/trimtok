"""trimtok v2 Phase 1 tests: SQLite CCR aliases, deadline guard, marker refs.

Script style like test_core.py. Run: python tests/test_v2_phase1.py
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccr_v2
from bpe_estimator import estimate_bpe_tokens
from ccr_v2 import FastCCRStore
from core import compress_terminal
from dedup import compress_tool_result
from guard import Deadline, fail_open_guard
from marker import SENTINEL_RE, find_hashes, seen_line, seen_lines, terminal_head


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1
check.failed = 0


class _Ctx:
    """Config stub: defaults for everything, usage off (no file writes)."""
    @staticmethod
    def get_config(key, default):
        return False if key == "usage_enabled" else default


# ---------------------------------------------------------------------------
# 1. CCR v2: alias + sha refs, persistence, fallback, stats/purge
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    content = "line alpha\nline beta\n" + "payload " * 200
    ref = ccr_v2.put(content, dir=d)
    check("v2-put-alias", ref.startswith("#c") and len(ref) <= 5)
    check("v2-get-by-alias", ccr_v2.get(ref, dir=d) == content)
    sha12 = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:12]
    check("v2-get-by-sha", ccr_v2.get(sha12, dir=d) == content)
    check("v2-get-by-hash-prefixed", ccr_v2.get("#" + sha12, dir=d) == content)
    check("v2-stable-alias", ccr_v2.put(content, dir=d) == ref)
    check("v2-missing", ccr_v2.get("deadbeef1234", dir=d) is None)
    check("v2-bad-ref", ccr_v2.get("!!!", dir=d) is None)

    store2 = FastCCRStore(d)
    check("v2-persist-alias", store2.get(ref) == content)
    check("v2-persist-sha", store2.get(sha12) == content)

    with patch("ccr_v2.sqlite3.connect", side_effect=sqlite3.OperationalError("locked")):
        store3 = FastCCRStore(d / "fb")
    ref3 = store3.put("fallback content")
    check("v2-fallback-ref", ref3.startswith("#") and len(ref3) == 13)
    check("v2-fallback-get", store3.get(ref3) == "fallback content")
    check("v2-fallback-file", (d / "fb" / (ref3[1:] + ".txt")).exists())

    st = ccr_v2.stats(dir=d)
    check("v2-stats", st["entries"] >= 1 and st["bytes"] > 0)
    st3 = store3.stats()
    check("v2-stats-fallback-tier", st3["entries"] >= 1 and st3["bytes"] > 0)
    purged = ccr_v2.purge(dir=d)
    check("v2-purge", purged >= 1 and ccr_v2.get(ref, dir=d) is None)
    store2.close()
    store3.close()
    ccr_v2.close(dir=d)
    ccr_v2.close(dir=d / "fb")

sp = Path(tempfile.mkdtemp())
check("v2-surrogate", isinstance(ccr_v2.put("\ud800 lone", dir=sp), str))
ccr_v2.close(dir=sp)

# ---------------------------------------------------------------------------
# 2. Deadline + fail-open guard
# ---------------------------------------------------------------------------
dl = Deadline(0.5)
time.sleep(0.003)
raised = False
try:
    dl.check()
except TimeoutError:
    raised = True
check("guard-deadline-raises", raised)

big_log = "\n".join(["2026-01-01 00:00:00 INFO heartbeat ok"] * 3000) + "\nERROR final fail\n"
expired = Deadline(0.5)
time.sleep(0.003)
out_t, st_t = compress_terminal("tail", big_log, deadline=expired)
check("guard-terminal-timeout", st_t["gate"] == "passthrough_timeout" and out_t == big_log)

payload = json.dumps({"items": [{"id": i, "name": "x" * 40} for i in range(100)]}, indent=2)
expired2 = Deadline(0.5)
time.sleep(0.003)
out_r, st_r = compress_tool_result("search_files", payload, session_id="guard-s", deadline=expired2)
check("guard-tool-timeout", st_r["gate"] == "passthrough_timeout" and out_r == payload)


@fail_open_guard(50.0)
def _shrink(ctx):
    return ctx.raw_text[:100]


check("guard-failopen-ok", _shrink("x" * 1000) == "x" * 100)


@fail_open_guard(50.0)
def _boom(ctx):
    raise RuntimeError("boom")


check("guard-failopen-exc", _boom("y" * 1000) == "y" * 1000)


@fail_open_guard(50.0)
def _grow(ctx):
    return ctx.raw_text + "extra"


check("guard-never-inflate", _grow("z" * 1000) == "z" * 1000)
check("guard-verbatim-tool", _shrink("v" * 1000, tool_name="read_file") == "v" * 1000)
check("guard-short-skip", _shrink("short") == "short")

# plugin-level: verbatim tools bypass compression before any work happens
import __init__ as plugin

plugin._on_tool_result._ctx = _Ctx()
check("plugin-verbatim-bypass", plugin._on_tool_result(tool_name="apply_patch", result="A" * 5000) is None)

# ---------------------------------------------------------------------------
# 3. Marker refs: compact pointers + sentinel across generations
# ---------------------------------------------------------------------------
check("marker-seen-line", seen_line("c1") == "[@#c1]")
check("marker-seen-lines", seen_lines(5, "c1") == "[@#c1x5]")
check("marker-terminal", terminal_head("log", 42, "c1") == "[tt:terminal kind=log hidden=42 #c1]")
check("marker-find-both", find_hashes("[@#c1] x [tt:tool kind=result saved=5 retrieve:abcdef123456]") == ["c1", "abcdef123456"])
check("sentinel-legacy", bool(SENTINEL_RE.search("[tt:seen line retrieve:abc123def456]")))
check("sentinel-pointer", bool(SENTINEL_RE.search("folded [@#c1x60] here")))

# ---------------------------------------------------------------------------
# 4. End-to-end: hex refs swap to alias; retrieve by alias AND by sha
# ---------------------------------------------------------------------------
raw = "raw payload\n" + "x" * 3000
digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
compressed = f"[tt:tool kind=result saved=3000 #{digest}]\n[@#{digest}x5]"
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    plugin._handle_retrieve._ctx = _Ctx()
    with patch.object(plugin, "ccr_put", side_effect=lambda content, **kw: ccr_v2.put(content, dir=d)), \
         patch.object(plugin, "ccr_get", side_effect=lambda ref, **kw: ccr_v2.get(ref, dir=d, **kw)):
        result = plugin._store_original(raw, compressed, digest, _Ctx())
        check("swap-all-refs", result is not None and result[0].count(result[1]) == 2 and digest not in result[0])
        alias = result[1] if result else ""
        got = json.loads(plugin._handle_retrieve({"hash": alias}))
        check("retrieve-by-alias", got.get("original_content") == raw)
        got2 = json.loads(plugin._handle_retrieve({"hash": "#" + digest}))
        check("retrieve-by-sha-hash", got2.get("original_content") == raw)
        got3 = json.loads(plugin._handle_retrieve({"hash": "tt:retrieve:" + alias}))
        check("parse-forms", got3.get("original_content") == raw)
        ccr_v2.close(dir=d)

# ---------------------------------------------------------------------------
# 5. Stub token reduction (measured, plan Phase 1 target)
# ---------------------------------------------------------------------------
pairs = [
    ("seen-single", "[tt:seen line retrieve:a1b2c3d4e5f6]", seen_line("c1")),
    ("seen-span", "[tt:seen lines=60 retrieve:a1b2c3d4e5f6]", seen_lines(60, "c1")),
    ("template", "[tt:template lines=119 retrieve:a1b2c3d4e5f6]", "[tt:template lines=119 #c1]"),
    ("shingle", "[tt:shingle lines=1 retrieve:a1b2c3d4e5f6]", "[tt:shingle #c1]"),
    ("headtail", "[tt:headtail head=30 tail=30 omitted=500 retrieve:a1b2c3d4e5f6]", "[tt:headtail omitted=500 #c1]"),
]
for name, old, new in pairs:
    to, tn = estimate_bpe_tokens(old), estimate_bpe_tokens(new)
    pct = 100 * (to - tn) // to if to else 0
    print(f"  stub {name}: ~{to} -> ~{tn} tokens (-{pct}%)")
check("stub-reduction-single", estimate_bpe_tokens(seen_line("c1")) <= estimate_bpe_tokens(pairs[0][1]) * 0.6)
check("stub-reduction-span", estimate_bpe_tokens(seen_lines(60, "c1")) <= estimate_bpe_tokens(pairs[1][1]) * 0.6)

print(f"\n{check.failed} failures")
sys.exit(1 if check.failed else 0)
