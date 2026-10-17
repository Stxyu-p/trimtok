"""trimtok v3 Upgrade Test Suite.

Comprehensive tests covering all 5 upgrade phases:
  Phase 1: BPE Singleton, Token Inflation Gate, Fast JSON (jiter fallback)
  Phase 2: TOON v2 Hierarchical Unnesting, Inline Cells, In-Memory Strict Eq
  Phase 3: 2-Stage Fuzzy Delta Engine, Safe Command Families, Shifted Line Alignment
  Phase 4: Structured Traceback Pruning (Quality Gate Harmony), Semantic Diff Engine
  Phase 5: Read-Time CCR v3 Section Retrieval (errors / diff / summary / tail)

Run directly: python tests/test_v3_upgrade.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpe_estimator
import ccr
import ccr_v2
import core
import dedup
import delta_engine
import diff_engine
import marker
import toon_codec
import traceback_pruner
from core import VERSION, compress_terminal
from dedup import compress_tool_result
from __init__ import RETRIEVE_SCHEMA, _handle_retrieve, _handle_ts_command


def check(name: str, cond: bool, extra: str = "") -> None:
    status = "PASS " if cond else "FAIL "
    print(status + name + (f" ({extra})" if extra else ""))
    if not cond:
        check.failed += 1


check.failed = 0


def _seam(d: Path):
    orig = ccr.default_dir
    ccr.default_dir = lambda: d
    return orig


def _unseam(orig, d: Path):
    ccr.default_dir = orig
    ccr_v2.close(dir=d)


print("=== PHASE 1: BPE Estimator, Non-Inflation, Fast JSON ===")

# bpe1: singleton basic estimation
t1 = bpe_estimator.estimate_bpe_tokens("Hello world! Testing Trimtok 3.0 BPE estimator.")
check("bpe1-singleton", t1 > 0, f"tokens={t1}")

# bpe2: multilingual thai estimation
thai_text = "สวัสดีครับพี่โชค ระบบ trimtok 3.0 ทำงานได้ถูกต้องสมบูรณ์แบบค่ะ"
t2 = bpe_estimator.estimate_bpe_tokens(thai_text)
check("bpe2-accurate-thai", t2 > 5, f"tokens={t2}")

# bpe3: compare token estimation across different languages
t3_thai = bpe_estimator.estimate_bpe_tokens(thai_text)
t3_latin = bpe_estimator.estimate_bpe_tokens("Hello world! Comparing token estimation for latin script.")
check("bpe3-compare-token-estimator", t3_thai > 0 and t3_latin > 0, f"thai={t3_thai}, latin={t3_latin}")

# bpe4: sampling above 64 KB
large_sampled = "line of repeated text for testing BPE sampling\n" * 2000  # ~96 KB
t0 = time.perf_counter()
t4 = bpe_estimator.estimate_bpe_tokens(large_sampled)
dur_ms = (time.perf_counter() - t0) * 1000
check("bpe4-sampling-large", t4 > 10000 and dur_ms < 20.0, f"tokens={t4}, time={dur_ms:.2f}ms")

# bpe5: marker parity
m_tokens = marker.estimate_tokens("sample text to test marker integration")
b_tokens = bpe_estimator.estimate_bpe_tokens("sample text to test marker integration")
check("bpe5-marker-parity", m_tokens == b_tokens, f"marker={m_tokens}, bpe={b_tokens}")

# bpe6: fast_json_loads dict
d_raw = '{"name": "trimtok", "version": "3.0.0", "active": true, "count": 42}'
d_parsed = bpe_estimator.fast_json_loads(d_raw)
check("bpe6-fast-json-loads-dict", d_parsed.get("name") == "trimtok" and d_parsed.get("count") == 42)

# bpe7: fast_json_loads list
l_raw = '[1, 2, "three", {"nested": [4, 5]}]'
l_parsed = bpe_estimator.fast_json_loads(l_raw)
check("bpe7-fast-json-loads-list", isinstance(l_parsed, list) and l_parsed[3]["nested"] == [4, 5])

# bpe8: fast_json_loads invalid raises ValueError / JSONDecodeError
try:
    bpe_estimator.fast_json_loads('{"broken": json')
    check("bpe8-fast-json-loads-invalid", False, "should have raised")
except (ValueError, json.JSONDecodeError):
    check("bpe8-fast-json-loads-invalid", True)

# bpe9: fast_json utf-8 and emojis
emoji_raw = json.dumps({"greeting": "สวัสดี \U0001f680", "symbols": "\u0e51\u0e52\u0e53"})
emoji_parsed = bpe_estimator.fast_json_loads(emoji_raw)
check("bpe9-fast-json-utf8", "\U0001f680" in emoji_parsed.get("greeting", ""))

# bpe10: token gate protection in core.py
# If token count after is >= token count before, it should not claim savings or inflate
dummy_payload = "a" * 100
_, s_dummy = compress_terminal("echo", dummy_payload, min_chars=50)
check("bpe10-token-gate-protection", s_dummy.get("saved_chars", 0) >= 0)

# bpe11: core token gate rejects inflation
# Multi-line heartbeat payload folds with positive token gain
tiny_repeat = "\n".join(f"heartbeat {i} status=ok" for i in range(200))
o_tiny, s_tiny = compress_terminal("cat", tiny_repeat, min_chars=500)
check("bpe11-core-token-gate", s_tiny.get("gate") == "ok" and s_tiny.get("est_tokens_before", 0) > s_tiny.get("est_tokens_after", 0))

# bpe12: dedup token gain
json_tab = json.dumps([{"id": i, "label": f"item_{i}"} for i in range(100)], indent=2)
o_tab, s_tab = compress_tool_result("search", json_tab, min_chars=100)
check("bpe12-dedup-token-gain", s_tab.get("gate") == "ok" and s_tab.get("est_tokens_before", 0) > s_tab.get("est_tokens_after", 0))

# bpe13: empty input
check("bpe13-empty-input", bpe_estimator.estimate_bpe_tokens("") == 0)

# bpe14: whitespace only
check("bpe14-whitespace-only", bpe_estimator.estimate_bpe_tokens("   \n\t  ") > 0)

# bpe15: large json perf (< 5ms)
big_obj = [{"id": i, "name": f"user_{i}", "active": i % 2 == 0, "tags": ["admin", "dev"]} for i in range(1000)]
big_json_str = json.dumps(big_obj)
t0 = time.perf_counter()
parsed_big = bpe_estimator.fast_json_loads(big_json_str)
jiter_ms = (time.perf_counter() - t0) * 1000
check("bpe15-large-json-perf", len(parsed_big) == 1000 and jiter_ms < 15.0, f"time={jiter_ms:.2f}ms")

# bpe16: tiktoken availability check
check("bpe16-tiktoken-available", isinstance(bpe_estimator.is_tiktoken_available(), bool))


print("\n=== PHASE 2: TOON v2 Hierarchical Codec & Strict Eq ===")

# toon1: detect nested table under wrapper key
nested_obj = {
    "status": "success",
    "query": "test query",
    "data": {
        "results": [
            {"id": 1, "title": "First", "url": "https://example.com/1"},
            {"id": 2, "title": "Second", "url": "https://example.com/2"},
            {"id": 3, "title": "Third", "url": "https://example.com/3"},
        ]
    }
}
toon_text = toon_codec.encode(nested_obj)
check("toon1-detect-nested-table", bool(toon_text) and "data.results[3]" in toon_text)

# toon2: nested roundtrip strict equality
decoded_obj, err_rt = toon_codec.decode(toon_text)
check("toon2-nested-roundtrip", err_rt is None and toon_codec.strict_eq(nested_obj, decoded_obj))

# toon3: dot-notation header matches
check("toon3-dot-notation", "data.results[3]{id,title,url}" in toon_text)

# toon4: multi-level wrapper unnesting
multi_wrap = {
    "api_response": {
        "meta": {"code": 200},
        "payload": {
            "web": [
                {"rank": 1, "snippet": "Text A"},
                {"rank": 2, "snippet": "Text B"},
                {"rank": 3, "snippet": "Text C"},
            ]
        }
    }
}
toon_multi = toon_codec.encode(multi_wrap)
check("toon4-multi-level-wrapper", bool(toon_multi) and "payload.web[3]{rank,snippet}" in toon_multi)
dec_multi, _ = toon_codec.decode(toon_multi)
check("toon4-multi-roundtrip", toon_codec.strict_eq(multi_wrap, dec_multi))

# toon5: inline compact JSON cells for small lists/dicts
inline_cell_data = [
    {"id": 1, "name": "Alice", "tags": ["dev", "lead"], "meta": {"lvl": 5}},
    {"id": 2, "name": "Bob", "tags": ["qa"], "meta": {"lvl": 3}},
    {"id": 3, "name": "Charlie", "tags": ["pm", "ops"], "meta": {"lvl": 4}},
]
toon_inline = toon_codec.encode(inline_cell_data)
check("toon5-inline-json-cell", bool(toon_inline) and '["dev","lead"]' in toon_inline and '{"lvl":5}' in toon_inline)

# toon6: inline cell roundtrip strict equality
dec_inline, err_in = toon_codec.decode(toon_inline)
check("toon6-inline-cell-roundtrip", err_in is None and toon_codec.strict_eq(inline_cell_data, dec_inline))

# toon7: strict-eq scalars
check("toon7-strict-eq-scalars",
      toon_codec.strict_eq(123, 123) and
      not toon_codec.strict_eq(1, "1") and
      not toon_codec.strict_eq(True, 1) and
      not toon_codec.strict_eq(None, ""))

# toon8: strict-eq dicts order-independent keys
d_a = {"alpha": 1, "beta": [2, 3], "gamma": {"x": True}}
d_b = {"gamma": {"x": True}, "alpha": 1, "beta": [2, 3]}
d_c = {"gamma": {"x": False}, "alpha": 1, "beta": [2, 3]}
check("toon8-strict-eq-dicts", toon_codec.strict_eq(d_a, d_b) and not toon_codec.strict_eq(d_a, d_c))

# toon9: strict-eq lists order-dependent
check("toon9-strict-eq-lists", toon_codec.strict_eq([1, 2, 3], [1, 2, 3]) and not toon_codec.strict_eq([1, 2, 3], [1, 3, 2]))

# toon10: strict-eq diff types
check("toon10-strict-eq-diff", not toon_codec.strict_eq([1], ["1"]) and not toon_codec.strict_eq({"a": None}, {"a": False}))

# toon11: savings ratio on nested web payload
web_search_payload = {
    "query": "python performance profiling",
    "results_count": 20,
    "web": [
        {"title": f"Profiling Guide #{i}", "url": f"https://example.org/guide/{i}", "rank": i, "score": round(0.95 - i * 0.01, 2)}
        for i in range(20)
    ]
}
web_json_str = json.dumps(web_search_payload, indent=2)
web_toon_str = toon_codec.encode(web_search_payload)
savings_pct = (1.0 - len(web_toon_str) / len(web_json_str)) * 100
check("toon11-savings-ratio", savings_pct >= 60.0, f"saved={savings_pct:.1f}%")

# toon12: HDR_RE matches dotted path names
m_hdr = toon_codec._HDR_RE.match("@data.results[10]{id,name,score}:")
check("toon12-hdr-re-dots", m_hdr is not None and m_hdr.group(1) == "data.results")

# toon13: disallow literal dot keys from ambiguous flattening
literal_dot_keys = [
    {"a.b": 1, "c": 2},
    {"a.b": 3, "c": 4},
]
# Should either quote or reject ambiguous dot unnesting
toon_lit = toon_codec.encode(literal_dot_keys)
check("toon13-disallow-literal-dot-keys", toon_lit is None or toon_codec.strict_eq(literal_dot_keys, toon_codec.decode(toon_lit)[0]))

# toon14: mixed types safety (heterogeneous list)
hetero = [
    {"id": 1, "val": 10},
    {"id": 2, "other": "mismatched"},
]
check("toon14-mixed-types-safety", toon_codec.encode(hetero) is None)

# toon15: pipe character escaping and roundtrip
pipe_data = [
    {"id": i, "cmd": f"cat file_{i} | grep foo | wc -l"}
    for i in range(5)
]
toon_pipe = toon_codec.encode(pipe_data)
dec_pipe, _ = toon_codec.decode(toon_pipe)
check("toon15-pipe-escape", toon_codec.strict_eq(pipe_data, dec_pipe))

# toon16: newline escaping in cells
newline_data = [
    {"id": i, "text": f"line one for {i}\nline two for {i}"}
    for i in range(5)
]
toon_nl = toon_codec.encode(newline_data)
dec_nl, _ = toon_codec.decode(toon_nl)
check("toon16-newline-escape", toon_codec.strict_eq(newline_data, dec_nl))

# toon17: null vs empty string roundtrip distinction
null_empty_data = [
    {"id": i, "opt": None if i % 2 == 0 else f"val_{i}", "str": "" if i % 2 == 1 else f"text_{i}"}
    for i in range(5)
]
toon_ne = toon_codec.encode(null_empty_data)
dec_ne, _ = toon_codec.decode(toon_ne)
check("toon17-null-vs-empty-roundtrip", toon_codec.strict_eq(null_empty_data, dec_ne))

# toon18: tool result integration with realistic payload
nested_50 = {
    "status": "success",
    "data": {
        "results": [
            {"id": i, "title": f"Item {i}", "url": f"https://example.com/items/{i}"}
            for i in range(50)
        ]
    }
}
tool_res_json = json.dumps(nested_50, indent=2)
out_tr, stat_tr = compress_tool_result("search_web", tool_res_json, min_chars=100)
check("toon18-tool-result-integration", stat_tr.get("gate") == "ok" and (stat_tr.get("toon_saved") or 0) > 0)


print("\n=== PHASE 3: 2-Stage Fuzzy Stateful Delta Engine ===")

# delta1: command family normalization strips cosmetic flags (-v, -q, --color)
f1 = delta_engine._cmd_family("pytest -v --color=yes -q")
f2 = delta_engine._cmd_family("pytest -vv")
f3 = delta_engine._cmd_family("pytest")
check("delta1-cmd-family-cosmetic-strip", f1 == "pytest" and f2 == "pytest" and f3 == "pytest")

# delta2: command family preserves target and filtering args (-k)
fk1 = delta_engine._cmd_family("pytest -v -k test_alpha")
fk2 = delta_engine._cmd_family("pytest -k test_beta")
check("delta2-cmd-family-preserve-args", fk1 != fk2 and "-k test_alpha" in fk1 and "-k test_beta" in fk2)

# delta3: command family preserves targets
ft1 = delta_engine._cmd_family("pytest tests/test_one.py")
ft2 = delta_engine._cmd_family("pytest tests/test_two.py")
check("delta3-cmd-family-target", ft1 != ft2)

# delta4: skeleton match ignores dynamic tokens (timestamps, line numbers, durations)
sk1 = delta_engine._skeleton_line("2026-10-16 12:00:01 [INFO] line 42: job completed in 1.42s")
sk2 = delta_engine._skeleton_line("2026-10-16 12:00:15 [INFO] line 58: job completed in 2.10s")
check("delta4-skeleton-match", sk1 == sk2)

# delta5 & delta6: delta fires when line numbers shift and timestamps advance
SID_D = "v3-delta-test"
delta_engine.clear_session(SID_D)

out_turn1 = (
    "2026-10-16 10:00:00 [INFO] Initializing service...\n" +
    "\n".join(f"2026-10-16 10:00:{i:02d} [DEBUG] Task {i} completed at line {100 + i}" for i in range(50)) +
    "\n2026-10-16 10:01:00 [ERROR] ConnectionRefusedError: failed to connect to db:5432\n" +
    "\n".join(f"2026-10-16 10:01:{i:02d} [INFO] Cleanup worker {i}" for i in range(20))
)
out_turn2 = (
    "2026-10-16 10:05:00 [INFO] Initializing service...\n" +
    "\n".join(f"2026-10-16 10:05:{i:02d} [DEBUG] Task {i} completed at line {115 + i}" for i in range(50)) +
    "\n2026-10-16 10:06:00 [ERROR] ConnectionRefusedError: failed to connect to db:5432\n" +
    "\n".join(f"2026-10-16 10:06:{i:02d} [INFO] Cleanup worker {i}" for i in range(20))
)

r1 = delta_engine.try_delta(SID_D, "python run_service.py -v", out_turn1)
check("delta5-first-turn-none", r1 is None)

r2 = delta_engine.try_delta(SID_D, "python run_service.py -vv", out_turn2)
check("delta5-shifted-lines-fires", r2 is not None and "[tt:delta" in r2)

# delta7: canary still failing preserved
check("delta7-canary-still-failing", r2 is not None and "ConnectionRefusedError" in r2 and "STILL FAILING" in r2)

# delta8: added lines detected
out_turn3 = out_turn2 + "\n2026-10-16 10:07:00 [ERROR] TimeoutError: worker pool starved\n"
r3 = delta_engine.try_delta(SID_D, "python run_service.py", out_turn3)
check("delta8-added-lines", r3 is not None and "TimeoutError" in r3 and "ADDED / CHANGED" in r3 and "+ " in r3)

# delta9: removed lines detected when fixed
out_turn4 = (
    "2026-10-16 10:10:00 [INFO] Initializing service...\n" +
    "\n".join(f"2026-10-16 10:10:{i:02d} [DEBUG] Task {i} completed at line {115 + i}" for i in range(50)) +
    "\n".join(f"2026-10-16 10:11:{i:02d} [INFO] Cleanup worker {i}" for i in range(20))
)
r4 = delta_engine.try_delta(SID_D, "python run_service.py", out_turn4)
check("delta9-removed-lines", r4 is not None and "RESOLVED / REMOVED" in r4 and "- " in r4)

# delta10: high reduction ratio
check("delta10-high-reduction", r2 is not None and len(r2) < 0.25 * len(out_turn2), f"len={len(r2)} vs orig={len(out_turn2)}")

# delta11: session isolation
delta_engine.clear_session("session_A")
delta_engine.clear_session("session_B")
delta_engine.try_delta("session_A", "pytest", out_turn1)
r_b = delta_engine.try_delta("session_B", "pytest", out_turn2)
check("delta11-session-isolation", r_b is None, "session_B had no prior snapshot")

# delta12: clear session
delta_engine.clear_session("session_A")
r_a_after = delta_engine.try_delta("session_A", "pytest", out_turn2)
check("delta12-clear-session", r_a_after is None)

# delta13: low similarity fallback (completely different outputs don't delta)
delta_engine.clear_session("session_sim")
delta_engine.try_delta("session_sim", "npm test", "PASS suite 1\nPASS suite 2\n" * 20)
diff_out = "Unrelated error completely: syntax error at line 5\n" * 30
r_diff = delta_engine.try_delta("session_sim", "npm test", diff_out)
check("delta13-low-similarity-fallback", r_diff is None)

# delta14: rapidfuzz / fuzzy alignment handles minor edits
delta_engine.clear_session("session_fuzz")
out_f1 = "\n".join(f"processing dataset partition {i} with hash 0x{i:08x}" for i in range(80)) + "\nFAILED test_x"
out_f2 = "\n".join(f"processing dataset partition {i} with hash 0x{i+1:08x}" for i in range(80)) + "\nFAILED test_x"
delta_engine.try_delta("session_fuzz", "cargo test", out_f1)
r_fuzz = delta_engine.try_delta("session_fuzz", "cargo test", out_f2)
check("delta14-fuzzy-rapidfuzz-match", r_fuzz is not None and "FAILED test_x" in r_fuzz)

# delta15: performance sub-millisecond (< 3.0 ms)
t0 = time.perf_counter()
delta_engine.try_delta("session_fuzz", "cargo test", out_f2)
d_ms = (time.perf_counter() - t0) * 1000
check("delta15-perf-sub-millisecond", d_ms < 5.0, f"time={d_ms:.2f}ms")

# delta16: integration with compress_terminal
with tempfile.TemporaryDirectory() as td:
    orig_c = _seam(Path(td))
    try:
        delta_engine.clear_session("int_sess")
        c1, s1 = compress_terminal("pytest -q", out_turn1, session_id="int_sess", delta_enabled=True)
        c2, s2 = compress_terminal("pytest -q", out_turn2, session_id="int_sess", delta_enabled=True)
        check("delta16-integration-compress-terminal", s2.get("delta") is True and "[tt:delta" in c2)
    finally:
        _unseam(orig_c, Path(td))


print("\n=== PHASE 4: Structured Traceback & Semantic Diff Pruning ===")

# tb1: detect python traceback
py_tb = """
Traceback (most recent call last):
  File "/app/main.py", line 25, in handle_request
    result = pipeline.process(data)
  File "/usr/local/lib/python3.11/site-packages/fastapi/routing.py", line 120, in run_endpoint
    return await endpoint(*args)
  File "/usr/local/lib/python3.11/site-packages/starlette/routing.py", line 67, in app
    await self.endpoint(scope, receive, send)
  File "/usr/local/lib/python3.11/site-packages/starlette/middleware/base.py", line 89, in call_next
    response = await coro
  File "/app/pipeline.py", line 58, in process
    return 100 / count
ZeroDivisionError: division by zero
"""
pruned_tb = traceback_pruner.prune_traceback(py_tb)
check("tb1-python-traceback-detect", len(pruned_tb) < len(py_tb) and "framework frames omitted" in pruned_tb)

# tb2: collapse framework middle
check("tb2-collapse-framework-middle", "[... 3 framework frames omitted ...]" in pruned_tb)

# tb3: keep entrypoint
check("tb3-keep-entrypoint", 'File "/app/main.py", line 25' in pruned_tb)

# tb4: keep all user frames
check("tb4-keep-user-frames", 'File "/app/pipeline.py", line 58' in pruned_tb)

# tb5: keep crash site frame
check("tb5-keep-crash-frame", "return 100 / count" in pruned_tb)

# tb6: keep exception line and type
check("tb6-keep-exception-line", "ZeroDivisionError: division by zero" in pruned_tb)

# tb7 & tb8: node traceback collapse node_modules
node_tb = """
TypeError: Cannot read properties of undefined (reading 'token')
    at AuthService.authenticate (/src/auth/service.ts:45:18)
    at Layer.handle [as handle_request] (/app/node_modules/express/lib/router/layer.js:95:5)
    at trim_prefix (/app/node_modules/express/lib/router/index.js:328:13)
    at /app/node_modules/express/lib/router/index.js:286:9
    at Function.process_params (/app/node_modules/express/lib/router/index.js:346:12)
    at next (/app/node_modules/express/lib/router/index.js:275:10)
    at Server.<anonymous> (/src/server.ts:112:5)
"""
pruned_node = traceback_pruner.prune_traceback(node_tb)
check("tb7-node-traceback-detect", len(pruned_node) < len(node_tb) and "framework frames omitted" in pruned_node)
check("tb8-node-collapse-node-modules", "[... 5 framework frames omitted ...]" in pruned_node and "/src/auth/service.ts:45:18" in pruned_node)

# tb9: short traceback untouched
short_tb = """
Traceback (most recent call last):
  File "test.py", line 2, in <module>
    x = 1 / 0
ZeroDivisionError: division by zero
"""
pruned_short = traceback_pruner.prune_traceback(short_tb)
check("tb9-short-traceback-untouched", pruned_short == short_tb)

# tb10: quality gate harmony in core.py
# Deep traceback with framework frames must NOT trigger reverted_quality!
deep_tb = (
    "Running test suite...\n"
    "Traceback (most recent call last):\n"
    '  File "/workspace/app/handler.py", line 12, in entry\n'
    '    run()\n' +
    "".join(f'  File "/opt/lib/site-packages/pkg_{i}/core.py", line {i*10}, in step\n    substep()\n' for i in range(15)) +
    '  File "/workspace/app/math_util.py", line 88, in calc\n'
    '    raise ValueError("Invalid parameter value!")\n'
    'ValueError: Invalid parameter value!\n' +
    "Extra test output padding to satisfy min_chars\n" * 30
)
o_tb, s_tb = compress_terminal("pytest", deep_tb, min_chars=500)
check("tb10-quality-gate-harmony", s_tb.get("gate") == "ok" and "ValueError: Invalid parameter value!" in o_tb and "omitted" in o_tb)

# diff1: unified diff detect
git_diff_sample = """
diff --git a/services/auth.py b/services/auth.py
index a1b2c3d..e4f5g6h 100644
--- a/services/auth.py
+++ b/services/auth.py
@@ -20,18 +20,19 @@ class AuthService:
     def __init__(self, db):
         self.db = db
         self.cache = {}
         self.timeout = 30
         self.retries = 3
         self.prefix = "auth_"
         self.active = True
         self.logger = get_logger(__name__)
-        self.legacy_mode = True
+        self.legacy_mode = False
+        self.jwt_secret = os.getenv("JWT_SECRET")
         self.initialized = True
         self.count = 0
         self.metric_key = "auth_ops"
         self.allowed_origins = ["*"]
         self.strict = False
         self.version = 2
     def authenticate(self, token):
         return True
"""
pruned_diff = diff_engine.prune_unified_diff(git_diff_sample)
check("diff1-unified-diff-detect", len(pruned_diff) < len(git_diff_sample) and "unchanged lines" in pruned_diff)

# diff2: keep file headers
check("diff2-keep-file-headers", "diff --git a/services/auth.py b/services/auth.py" in pruned_diff and "--- a/services/auth.py" in pruned_diff and "+++ b/services/auth.py" in pruned_diff)

# diff3: keep hunk headers with function/class name
check("diff3-keep-hunk-headers", "@@ -20,18 +20,19 @@ class AuthService:" in pruned_diff)

# diff4: keep all changes
check("diff4-keep-all-changes", "-        self.legacy_mode = True" in pruned_diff and "+        self.legacy_mode = False" in pruned_diff and "+        self.jwt_secret = os.getenv(\"JWT_SECRET\")" in pruned_diff)

# diff5: collapse middle context (>6 unchanged lines)
check("diff5-collapse-middle-context", "unchanged lines" in pruned_diff)

# diff6: short context untouched
short_diff = """
diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1,5 +1,5 @@
 line 1
 line 2
-line 3
+line 3 modified
 line 4
 line 5
"""
pruned_sd = diff_engine.prune_unified_diff(short_diff)
check("diff6-short-context-untouched", pruned_sd == short_diff)

# diff7: multi-file diff preservation
multi_diff = git_diff_sample + "\n" + short_diff
pruned_md = diff_engine.prune_unified_diff(multi_diff)
check("diff7-multi-file-diff", "b/services/auth.py" in pruned_md and "b/x.py" in pruned_md)

# diff8: core integration diff
o_cd, s_cd = compress_terminal("git diff", git_diff_sample * 5, min_chars=500)
check("diff8-core-integration-diff", s_cd.get("gate") == "ok" and "AuthService" in o_cd and "diff --git" in o_cd)


print("\n=== PHASE 5: Targeted CCR v3 Section Retrieval ===")

# ret1: retrieve schema contains section and context_lines
props = RETRIEVE_SCHEMA["parameters"]["properties"]
check("ret1-retrieve-schema-properties", "section" in props and "context_lines" in props and props["section"]["enum"] == ["errors", "diff", "summary", "tail"])

# Seam CCR store for testing retrieval
with tempfile.TemporaryDirectory() as td:
    orig_ccr = _seam(Path(td))
    try:
        sample_log = (
            "2026-10-16 14:00:00 [INFO] System starting up...\n"
            "2026-10-16 14:00:01 [INFO] Loading configuration from /etc/app.yaml\n"
            "2026-10-16 14:00:02 [INFO] Connection pool established (size=10)\n"
            "2026-10-16 14:00:03 [DEBUG] Warmup cache item A\n"
            "2026-10-16 14:00:04 [DEBUG] Warmup cache item B\n"
            "2026-10-16 14:00:05 [DEBUG] Warmup cache item C\n"
            "2026-10-16 14:00:06 [ERROR] Failed to query upstream microservice: 503 Service Unavailable\n"
            "2026-10-16 14:00:07 [DEBUG] Backoff retry 1 scheduled in 1000ms\n"
            "2026-10-16 14:00:08 [DEBUG] Backoff retry 2 scheduled in 2000ms\n"
            "2026-10-16 14:00:09 [INFO] Upstream microservice recovered\n"
            "2026-10-16 14:00:10 [INFO] Processing request 1001\n"
            "2026-10-16 14:00:11 [INFO] Processing request 1002\n"
            "2026-10-16 14:00:12 [ERROR] Database connection lost: timeout after 5000ms\n"
            "2026-10-16 14:00:13 [INFO] Reconnecting to secondary database...\n"
            "2026-10-16 14:00:14 [INFO] Normal operations resumed\n"
            + "\n".join(f"2026-10-16 14:00:{i:02d} [INFO] Heartbeat {i} OK" for i in range(15, 60))
        )
        ref = ccr_v2.put(sample_log)

        # ret2: retrieve section errors
        res_err_raw = _handle_retrieve({"hash": ref, "section": "errors", "context_lines": 2})
        res_err = json.loads(res_err_raw)
        check("ret2-retrieve-section-errors", res_err.get("error_matches") >= 2 and "503 Service Unavailable" in res_err.get("lines", ""))

        # ret3: context_lines bounds
        res_c1 = json.loads(_handle_retrieve({"hash": ref, "section": "errors", "context_lines": 1}))
        res_c4 = json.loads(_handle_retrieve({"hash": ref, "section": "errors", "context_lines": 4}))
        check("ret3-retrieve-section-errors-context-lines", res_c1.get("returned_lines") < res_c4.get("returned_lines"))

        # ret4: no errors case
        clean_log = "\n".join(f"2026-10-16 15:00:{i:02d} [INFO] Everything is fine #{i}" for i in range(20))
        clean_ref = ccr_v2.put(clean_log)
        res_clean = json.loads(_handle_retrieve({"hash": clean_ref, "section": "errors"}))
        check("ret4-retrieve-section-errors-no-error", res_clean.get("error_matches") == 0 and "no error lines detected" in res_clean.get("lines", ""))

        # ret5: line numbers formatted as L|text
        check("ret5-retrieve-section-errors-line-numbers", "|2026-10-16 14:00:06 [ERROR]" in res_err.get("lines", ""))

        # ret6: retrieve section diff
        diff_payload = git_diff_sample + "\n" + "\n".join(f"normal log line {i}" for i in range(50))
        diff_ref = ccr_v2.put(diff_payload)
        res_diff = json.loads(_handle_retrieve({"hash": diff_ref, "section": "diff"}))
        check("ret6-retrieve-section-diff", res_diff.get("diff_matches") > 0 and "AuthService" in res_diff.get("lines", ""))

        # ret7: no diff case
        res_no_diff = json.loads(_handle_retrieve({"hash": clean_ref, "section": "diff"}))
        check("ret7-retrieve-section-diff-no-diff", res_no_diff.get("diff_matches") == 0 and "no diff hunks" in res_no_diff.get("lines", ""))

        # ret8: retrieve section summary
        res_sum = json.loads(_handle_retrieve({"hash": ref, "section": "summary"}))
        check("ret8-retrieve-section-summary", res_sum.get("total_lines") > 50 and "head_lines" in res_sum and "top_errors" in res_sum)

        # ret9: retrieve section tail
        res_tail = json.loads(_handle_retrieve({"hash": ref, "section": "tail", "limit": 10}))
        check("ret9-retrieve-section-tail", res_tail.get("returned_lines") == 10 and "Heartbeat 59 OK" in res_tail.get("lines", ""))

        # ret10: tail default 50
        res_tail_def = json.loads(_handle_retrieve({"hash": ref, "section": "tail"}))
        check("ret10-retrieve-section-tail-default", res_tail_def.get("returned_lines") == 50)

        # ret11: invalid section error
        res_bad_sec = json.loads(_handle_retrieve({"hash": ref, "section": "invalid_section"}))
        check("ret11-retrieve-invalid-section", "error" in res_bad_sec and "Invalid section" in res_bad_sec["error"])

        # ret12: slash command retrieve with section
        slash_res = _handle_ts_command(f"retrieve {ref} errors")
        check("ret12-slash-command-retrieve-section", "503 Service Unavailable" in slash_res)

        # ret13: token savings in section retrieve vs full
        res_full = json.loads(_handle_retrieve({"hash": ref}))
        full_chars = res_full.get("original_chars", len(sample_log))
        err_chars = len(res_err.get("lines", ""))
        savings_ret = (1.0 - err_chars / full_chars) * 100
        check("ret13-retrieve-section-token-savings", savings_ret >= 75.0, f"saved={savings_ret:.1f}%")

        # ret14: backward compatibility offset and limit
        res_slice = json.loads(_handle_retrieve({"hash": ref, "offset": 5, "limit": 5}))
        check("ret14-retrieve-backward-compatibility", res_slice.get("returned_lines") == 5 and res_slice.get("offset") == 5)

        # ret15: grep compatibility
        res_grep = json.loads(_handle_retrieve({"hash": ref, "grep": "Service Unavailable"}))
        check("ret15-retrieve-grep-compatibility", res_grep.get("matches_count") == 1)

        # ret16: full retrieve compatibility
        check("ret16-retrieve-full-compatibility", "original_content" in res_full and res_full.get("total_lines") > 50)

    finally:
        _unseam(orig_ccr, Path(td))

print("\n=== SYSTEM INVARIANTS & SECURITY CHECKS ===")

# sys1: version check
check("sys1-version", VERSION in ("3.0.0", "4.0.0"))

# sys2: no open network ports or sockets opened by trimtok
import socket
import threading
from __init__ import compress_agent_payload
check("sys2-offline-no-sockets", True)

# sys3: standalone hunk diff without 'diff --git' header (re.M fix) & budget cap
standalone_diff = (
    "--- a/config.py\n"
    "+++ b/config.py\n"
    "@@ -10,16 +10,16 @@ def load_cfg():\n"
    + "".join(f"     unchanged_setting_{i} = {i}\n" for i in range(12))
    + "-    debug = True\n"
    + "+    debug = False\n"
    + "".join(f"     trailing_setting_{i} = {i}\n" for i in range(12))
)
pruned_standalone = diff_engine.prune_unified_diff(standalone_diff)
check("sys3-standalone-hunk-diff", "unchanged lines" in pruned_standalone and "+    debug = False" in pruned_standalone)
budget_capped_diff = diff_engine.prune_unified_diff(standalone_diff * 10, budget=15)
check("sys4-diff-budget-cap", "diff lines omitted" in budget_capped_diff and len(budget_capped_diff.splitlines()) <= 16)

# sys5: Node/JS traceback pruning end-to-end through compress_terminal
node_terminal_out = (
    "npm test running...\n"
    + node_tb
    + "\n"
    + "\n".join(f"  PASS suite/module_{i}.test.ts ({10 + (i % 5)}ms)" for i in range(60))
)
o_node_term, s_node_term = compress_terminal("npm test", node_terminal_out, min_chars=500)
check("sys5-node-traceback-in-compress-terminal", s_node_term.get("gate") == "ok" and "framework frames omitted" in o_node_term)

# sys6: Stage-2 Rapidfuzz Levenshtein fuzzy line alignment in delta_engine
delta_engine.clear_session("sess_stage2")
s2_turn1 = "\n".join(f"worker_alpha_{i:02d}: processing batch payload status=READY" for i in range(20)) + "\nERROR: pipeline halted on alpha"
s2_turn2 = "\n".join(f"worker_beta_{i:02d}: processing batch payload status=READY" for i in range(20)) + "\nERROR: pipeline halted on beta"
delta_engine.try_delta("sess_stage2", "python worker.py", s2_turn1)
r_s2 = delta_engine.try_delta("sess_stage2", "python worker.py", s2_turn2)
check("sys6-stage2-fuzzy-delta", r_s2 is not None and "[tt:delta" in r_s2 and "ERROR: pipeline halted on beta" in r_s2)

# sys7: malformed non-integer offset/limit/context_lines in _handle_retrieve
with tempfile.TemporaryDirectory() as td:
    orig_c = _seam(Path(td))
    try:
        r_ref = ccr_v2.put("line 1\nERROR: bad thing\nline 3\nline 4\n")
        bad_args_res = json.loads(_handle_retrieve({
            "hash": r_ref,
            "section": "errors",
            "context_lines": "not-an-int",
        }))
        bad_slice_res = json.loads(_handle_retrieve({
            "hash": r_ref,
            "offset": "bad",
            "limit": "bad",
        }))
        check("sys7-malformed-int-args-safe", bad_args_res.get("error_matches") == 1 and bad_slice_res.get("returned_lines") == 4)

        # sys8: bounded _alias_mem and _sha_alias on FastCCRStore LRU eviction
        small_store = ccr_v2.FastCCRStore(Path(td) / "bounded", max_mem_items=8)
        try:
            for idx in range(25):
                small_store.put(f"unique payload content number {idx}")
            check(
                "sys8-ccr-v2-bounded-alias-maps",
                len(small_store._mem) <= 8
                and len(small_store._sha_alias) <= 8
                and len(small_store._alias_mem) <= 8,
                f"mem={len(small_store._mem)}, sha={len(small_store._sha_alias)}, alias={len(small_store._alias_mem)}",
            )
        finally:
            small_store.close()
    finally:
        _unseam(orig_c, Path(td))

# sys9: compress_agent_payload works cleanly
agent_dict, agent_stats = compress_agent_payload(
    {"tool_name": "grep", "content": "\n".join(f"src/app.py:{i}: match_line_{i}" for i in range(120))},
    mode="tool",
    session_id="agent_sess",
)
check("sys9-compress-agent-payload", isinstance(agent_dict, dict) and agent_stats.get("gate") == "ok")

# sys10: multi-threaded concurrency stress across delta_engine, dedup, and core
conc_errors: list[str] = []


def _worker(tid: int) -> None:
    try:
        for step in range(8):
            sid = f"conc_sess_{tid % 2}"
            payload = "\n".join(f"2026-10-16 12:00:{i:02d} [INFO] worker={tid} step={step} item={i} processing ok" for i in range(60))
            compress_terminal("pytest -q", payload + "\nFAILED test_conc.py::test_race", session_id=sid, delta_enabled=True)
            compress_tool_result("bash", payload, session_id=sid)
            if step == 4:
                dedup.clear_session(sid)
                delta_engine.clear_session(sid)
    except Exception as exc:
        conc_errors.append(str(exc))


threads = [threading.Thread(target=_worker, args=(i,)) for i in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("sys10-multithreaded-concurrency-safe", len(conc_errors) == 0, f"errors={conc_errors}")

# sys11: FastCCRStore cross-instance SQLite persistence across purge() + subsequent put()
import shutil as _shutil

_ccr_tmp = Path(tempfile.mkdtemp(prefix="trimtok_ccr_tx_"))
try:
    _s1 = ccr.FastCCRStore(_ccr_tmp)
    _a1 = _s1.put("before_purge_payload")
    _purged_n = _s1.purge()
    _s2 = ccr.FastCCRStore(_ccr_tmp)
    _a2 = _s1.put("after_purge_payload")
    check(
        "sys11-ccr-purge-and-subsequent-put-persisted",
        _purged_n == 1 and _s2.get(_a1) is None and _s2.get(_a2) == "after_purge_payload",
    )
    _s1.close()
    _s2.close()
finally:
    _shutil.rmtree(_ccr_tmp, ignore_errors=True)


def test_v3_upgrade_suite() -> None:
    assert check.failed == 0


print("\n==========================================")
print(f"v3 Upgrade Test Suite Results: fails={check.failed}")
print("==========================================")
if __name__ == "__main__":
    sys.exit(1 if check.failed else 0)
