"""trimtok v2 Phase 2 tests: polarity rails, canary fidelity, scoped search.

Script style like test_core.py. Run: python tests/test_v2_phase2.py
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccr_v2
import core
import marker
import polarity
from dedup import _fold_intra_templates, clear_session, compress_tool_result
from scoped_search import group_search_hits, looks_search_shaped


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1
check.failed = 0


# ---------------------------------------------------------------------------
# 1. polarity: one source of rails + HTTP-status faults
# ---------------------------------------------------------------------------
check("polarity-core-parity", core.is_important is marker.is_important)
check("polarity-status500", polarity.is_fault_line("item 3 status=500 request delta"))
check("polarity-status503", polarity.is_fault_line("api status_code:503 upstream"))
check("polarity-http502", polarity.is_fault_line("HTTP/1.1 502 bad gateway upstream"))
check("polarity-status200-clean", not polarity.is_fault_line("item 1 status=200 request delta"))
check("polarity-existing-rails", polarity.is_fault_line("ERROR boom") and polarity.is_fault_line("ข้อผิดพลาด: ดิสก์เต็ม"))
check("polarity-nonfault", not polarity.is_fault_line("errores base done ok"))

# ---------------------------------------------------------------------------
# 2. canary: a fault member inside a template group must stay visible
# ---------------------------------------------------------------------------
_canary_lines = [
    "item 1 status=200 request delta",
    "item 2 status=200 request delta",
    "item 3 status=500 request delta",
    "item 4 status=200 request delta",
    "item 5 status=200 request delta",
]
_c_out, _c_saved = _fold_intra_templates("\n".join(_canary_lines), owner="aabbccddeeff")
check("canary500-visible", "status=500" in _c_out)
check("canary500-still-saves", _c_saved > 0)
_c4 = "\n".join(_canary_lines[:4])
_c4_out, _ = _fold_intra_templates(_c4, owner="aabbccddeeff")
check("canary200-only-folds", _c4_out != _c4)

# ---------------------------------------------------------------------------
# 3. grouping unit: multi-file fixture
# ---------------------------------------------------------------------------
def _make_fixture():
    lines = []
    for n in range(100):
        lines.append(f"A.py:{n}:def fa_{n}(): return {n}")
    for n in range(50):
        lines.append(f"B.py:{n}:plain data row {n} value")
    return "\n".join(lines)


_fx = _make_fixture()
check("scoped-shaped", looks_search_shaped(_fx))
_g_out, _g_saved = group_search_hits(_fx, owner="aaaaaaaabbbb")
check("scoped-headers", "=== A.py (100 hits)" in _g_out and "=== B.py (50 hits)" in _g_out)
check("scoped-defs", "[defs: fa_0@L0 fa_1@L1 …+98]" in _g_out)
check("scoped-pointer", "[@#aaaaaaaabbbbx98]" in _g_out)
check("scoped-saves", _g_saved > 0 and len(_g_out) < len(_fx))

# canary inside the grouped middle
_fx_canary = _fx.split("\n")
_fx_canary.insert(55, "A.py:55:status=500 worker crash margin")
_gc_out, _gc_saved = group_search_hits("\n".join(_fx_canary), owner="aaaaaaaabbbb")
check("scoped-canary-visible", "status=500 worker crash margin" in _gc_out)

# single-file docs are out of scope (SIM-safety gate)
_sf = "\n".join(f"src/a.py:{n}:def func_{n}(): return {n}" for n in range(400))
_sf_res = group_search_hits(_sf, owner="x" * 12)
check("scoped-single-file-skip", not looks_search_shaped(_sf) and _sf_res == (_sf, 0))

# non-search text untouched
_ns = "speculative shuffling text line\n" * 100
check("scoped-nonsearch-skip", group_search_hits(_ns, owner="x" * 12)[1] == 0)

# ---------------------------------------------------------------------------
# 4. compress_tool_result integration: headers + canary + resolution
# ---------------------------------------------------------------------------
clear_session("p2a")
big_raw = "\n".join(
    f"src/m{i}.py:{n * 5 + 3}:def handle_{i}_{n}(payload): return step_{n}"
    for i in range(4)
    for n in range(60)
)
int_out, int_st = compress_tool_result("search_files", big_raw, session_id="p2a")
check("integration-gate", int_st["gate"] == "ok")
check("integration-headers", "=== src/m0.py (60 hits)" in int_out)
check("integration-pointer", "[@#" in int_out)
check("integration-search-saved", int_st.get("search_saved", 0) > 0)

d = hashlib.sha256(big_raw.encode("utf-8", "replace")).hexdigest()[:12]
with tempfile.TemporaryDirectory() as td:
    dd = Path(td)
    ref = ccr_v2.put(big_raw, dir=dd)
    check(
        "integration-resolves",
        ccr_v2.get(ref, dir=dd) == big_raw and ccr_v2.get(d, dir=dd) == big_raw,
    )
    ccr_v2.close(dir=dd)

# canary through the full pipeline
clear_session("p2b")
can_raw = big_raw.split("\n")
can_raw.insert(123, "src/m2.py:124:ERROR disk timeout failed hard")
can_out, can_st = compress_tool_result("search_files", "\n".join(can_raw), session_id="p2b")
check("integration-canary", can_st["gate"] == "ok" and "ERROR disk timeout failed hard" in can_out)

# ---------------------------------------------------------------------------
# 5. SIM-safety pin: the 5-turn SIM search fixture keeps template behavior
# ---------------------------------------------------------------------------
clear_session("sim-pin")
s1 = ("\n".join(f"src/a.py:{i}:def func_{i}(): return {i}" for i in range(400)))[:20_000]
pin_out, pin_st = compress_tool_result("search_files", s1, session_id="sim-pin")
check("sim-pin-no-header", "=== " not in pin_out)
check("sim-pin-collapse", pin_st["gate"] == "ok" and len(pin_out) < len(s1) * 0.02)

print(f"\n{check.failed} failures")
sys.exit(1 if check.failed else 0)
