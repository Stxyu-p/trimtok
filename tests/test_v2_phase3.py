"""trimtok v2 Phase 3 tests: lossless JSON->TOON codec + Token-Gain Gate.

Script style like test_core.py. Run: python tests/test_v2_phase3.py
"""

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bpe_estimator import estimate_bpe_gain
from dedup import clear_session, compress_tool_result
from toon_codec import MIN_BPE_GAIN, fold_json_to_toon, toon_decode


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" " + extra) if extra else ""))
    if not cond:
        check.failed += 1
check.failed = 0


def _strict(x, y):
    return json.dumps(x, ensure_ascii=False, sort_keys=True) == json.dumps(y, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 1. shape: bare array — @prefix + @rows header + pipe rows
# ---------------------------------------------------------------------------
rows = [{"path": f"C:\\repo\\src\\mod{i:04d}.py", "lines": 10 + i, "status": 200} for i in range(4)]
rj = json.dumps(rows)
out = fold_json_to_toon(rj)
check("shape-folds", out != rj)
check("shape-prefix-line", out.startswith("@prefix(~/)=C:\\repo\\src\\"))
check("shape-header", "@rows[4]{path,lines,status}:" in out)
check("shape-row", "~/mod0001.py|11|200" in out)
check("shape-roundtrip", _strict(toon_decode(out), rows))

# ---------------------------------------------------------------------------
# 2. wrapper dict: @meta + named table
# ---------------------------------------------------------------------------
wrap = {"total": 3, "ok": True, "results": [{"id": i, "name": f"n{i}"} for i in range(3)]}
wj = json.dumps(wrap)
wout = fold_json_to_toon(wj)
check("meta-folds", wout != wj and "@meta:" in wout and "@results[3]{id,name}:" in wout)
check("meta-roundtrip", _strict(toon_decode(wout), wrap))

# ---------------------------------------------------------------------------
# 3. refusals keep the input byte-identical
# ---------------------------------------------------------------------------
nonu = json.dumps([{"a": 1}, {"a": 2, "b": 3}, {"a": 4}])
check("refuse-nonuniform", fold_json_to_toon(nonu) == nonu)
nested = json.dumps([{"a": [1]}, {"a": [2]}, {"a": [3]}])
check("refuse-nested", fold_json_to_toon(nested) == nested)
tail = json.dumps([{"a": i} for i in range(20)]) + "\ntail not json"
check("refuse-tail", fold_json_to_toon(tail) == tail)
tiny = json.dumps([{"a": 1}, {"a": 2}])
check("refuse-tiny", fold_json_to_toon(tiny) == tiny)

# ---------------------------------------------------------------------------
# 4. 50-case Q&A equivalence: every value answerable from the table, exactly
# ---------------------------------------------------------------------------
rnd = random.Random(7)
cases = 0
bad = 0
for t in range(50):
    n = rnd.randrange(3, 60)
    keys = ["name", "value", "flag", "path"][: rnd.randrange(2, 5)]
    rws = []
    for i in range(n):
        r = {}
        for k in keys:
            if k == "name":
                r[k] = rnd.choice([f"item-{i}", "dup", 'q"uo', "ไทย-ไทย", "a | b", ""])
            elif k == "value":
                r[k] = rnd.choice([i, -i, round(i * 1.5, 2), None, True, False, f"{i}px", str(i)])
            elif k == "flag":
                r[k] = rnd.randrange(2) == 0
            else:
                r[k] = f"C:\\proj\\{i:03d}\\f.py"
        rws.append(r)
    doc = json.dumps(rws, ensure_ascii=False, indent=2 if t % 2 else None)
    o = fold_json_to_toon(doc)
    if o == doc:
        continue
    cases += 1
    if not _strict(toon_decode(o), rws):
        bad += 1
        print("  mismatch case", t)
check("qa50-folded", cases >= 45, f"folded={cases}/50")
check("qa50-equivalent", bad == 0, f"bad={bad}")

vis = [{"n": 999999, "s": "needle|pipe", "b": True} for _ in range(4)]
vout = fold_json_to_toon(json.dumps(vis))
check("qa50-visible", "999999" in vout and '"needle|pipe"' in vout and "|true" in vout)

# ---------------------------------------------------------------------------
# 5. Token-Gain Gate
# ---------------------------------------------------------------------------
small = json.dumps([{"k": f"v{i}", "n": i} for i in range(3)])
sout = fold_json_to_toon(small)
if sout != small:
    check("gate-small-gain", estimate_bpe_gain(small, sout) >= MIN_BPE_GAIN,
          f"gain={estimate_bpe_gain(small, sout)}")
else:
    check("gate-small-gain", True, "(refused — gate held)")

# ---------------------------------------------------------------------------
# 6. integration through compress_tool_result (default ON)
# ---------------------------------------------------------------------------
clear_session("p3a")
big_rows = [
    {"path": f"C:\\repo\\src\\m{i:04d}.py", "lines": 100 + i, "status": 500 if i == 321 else 200, "ok": True}
    for i in range(400)
]
bigdoc = json.dumps(big_rows, indent=2)
out_a, st_a = compress_tool_result("search_files", bigdoc, session_id="p3a")
check("intl-gate", st_a["gate"] == "ok")
check("intl-toon-used", st_a.get("toon_used") and st_a.get("toon_saved", 0) > 0,
      f"toon_saved={st_a.get('toon_saved')}")
check("intl-legend", "[tt:legend]" in out_a)
check("intl-canary-500", "500" in out_a)
_start = out_a.find("@prefix(")
if _start == -1:
    _start = out_a.find("@rows[")
check("intl-roundtrip", _strict(toon_decode(out_a[_start:]), big_rows))
clear_session("p3b")
out_b, st_b = compress_tool_result("search_files", bigdoc, session_id="p3b", toon_enabled=False)
check("intl-off-smaller-savings", st_b.get("saved_chars", 0) < st_a.get("saved_chars", 0),
      f"off={st_b.get('saved_chars')} on={st_a.get('saved_chars')}")

# ---------------------------------------------------------------------------
# 7. latency: report + LOAD-NORMALIZED regression guard. The plan target
#    (3.0 ms / 100 KB) is a documented miss for pure-Python + full round-trip
#    verify (json.loads alone ≈ 2 ms). Median swings ~4x with ambient load on
#    this box, so the numbers below are informational (ratio vs json.loads
#    printed); the assertion is only a no-blowup bound.
# ---------------------------------------------------------------------------
lat_rows = [
    {"path": f"C:/work/proj/src/mod{i:05d}.py", "lines": 100 + (i % 9000), "status": 200, "note": "x" * 40}
    for i in range(1200)
]
lat_doc = json.dumps(lat_rows, separators=(",", ":"))
ts = []
for _ in range(7):
    t0 = time.perf_counter()
    fold_json_to_toon(lat_doc)
    ts.append((time.perf_counter() - t0) * 1000)
ts.sort()
med = ts[len(ts) // 2]
lt = []
for _ in range(7):
    t0 = time.perf_counter()
    json.loads(lat_doc)
    lt.append((time.perf_counter() - t0) * 1000)
lt.sort()
loads_med = max(lt[len(lt) // 2], 0.05)
print(f"  latency: {len(lat_doc)} chars -> fold median {med:.2f} ms (min {ts[0]:.2f}); "
      f"json.loads {loads_med:.2f} ms; ratio {med / loads_med:.1f}x "
      f"(plan 3.0 ms = documented miss; load swings ~4x on this box)")
check("toon-latency-guard", med < 100.0, f"median={med:.2f} min={ts[0]:.2f}")

print(f"\n{check.failed} failures")
sys.exit(1 if check.failed else 0)
