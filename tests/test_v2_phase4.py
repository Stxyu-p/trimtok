"""trimtok v2 Phase 4 tests: stateful cross-turn command delta.

Plan canary (Regression Blindness Check): a test that failed in turn 1 and
still fails in turn 2 must stay visible under STILL FAILING.
Script style like test_core.py. Run: python tests/test_v2_phase4.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccr
import ccr_v2
from core import VERSION, compress_terminal
from delta_engine import clear_session, try_delta


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" " + extra) if extra else ""))
    if not cond:
        check.failed += 1
check.failed = 0


def _seam(d):
    orig = ccr.default_dir
    ccr.default_dir = lambda: d
    return orig


def _unseam(orig, d):
    ccr.default_dir = orig
    ccr_v2.close(dir=d)


SID = "p4-unit"
base = [f"test_{i:04d}.py::test_x PASSED" for i in range(200)]
err = "FAILED test_1000.py::test_boom - AssertionError: expected 1 got 2"
t1 = "\n".join(base + [err, "===== 1 failed, 200 passed in 42.1s ====="])
t2 = "\n".join(base + [err, "===== 1 failed, 200 passed in 43.2s ====="])

print("=== engine unit: fire / fault rail / threshold / caps ===")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    orig = _seam(d)
    try:
        clear_session(SID)
        r1 = try_delta(SID, "pytest -q", t1)
        check("u1-first-none", r1 is None)
        r2 = try_delta(SID, "pytest -q", t2)
        head2 = r2.split("]")[0] if r2 else ""
        check("u2-fires", bool(r2) and r2.startswith("[tt:delta"))
        check("u2-counts", "+#1 -#1 !#1" in head2)
        check("u2-canary", err in r2 and "STILL FAILING" in r2)
        check("u2-added", "43.2s" in r2 and r2.index("43.2s") > r2.index("ADDED"))
        check("u2-removed", "- ===== 1 failed, 200 passed in 42.1s =====" in r2)
        check("u2-refs", head2.count("#") >= 2)
        check("u2-small", len(r2) < len(t2) // 5)

        # chain: the next run compares against the previous one, not the first
        t3 = t2.replace("43.2s", "44.0s")
        r3 = try_delta(SID, "pytest -q", t3)
        check("u3-chain", bool(r3) and err in r3 and "44.0s" in r3 and "43.2s" in r3)

        # low similarity -> no delta, but the snapshot still updates
        tbig = "\n".join(f"unique line {i} payload {i * 7}" for i in range(200))
        r4 = try_delta(SID, "pytest -q", tbig)
        check("u4-low-sim-none", r4 is None)
        tbig2 = tbig + "\nappended marker line"
        r5 = try_delta(SID, "pytest -q", tbig2)
        check("u4-state-updated", bool(r5) and "appended marker line" in r5)

        # command and session isolation
        check("u5-cmd-isolated", try_delta(SID, "git status", t1) is None)
        check("u6-session-isolated", try_delta("other", "pytest -q", t1) is None)

        # caps: added >40 (faults first), removed >15, still >25
        ch = "caps-cmd"
        c1 = "\n".join(base + [err, "===== 1 failed, 200 passed in 40.0s ====="])
        cls = list(base)
        cls[3] = "NEW-03 payload"
        cls[30] = "FAILED test_30 - newly broken"
        c2 = "\n".join(cls + [f"NEW-{i:02d} payload" for i in range(40)] + [err, "===== 2 failed, 200 passed in 41.0s ====="])
        clear_session(SID)
        try_delta(SID, ch, c1)
        r6 = try_delta(SID, ch, c2)
        check("u7-added-cap", bool(r6) and "--- ADDED / CHANGED IN THIS TURN (+43) ---" in r6 and ".. (+3 more in" in r6)
        check("u7-faults-first", bool(r6) and r6.index("FAILED test_30 - newly broken") < r6.index("NEW-00 payload"))

        g1 = "\n".join(f"g{i:03d} modified" for i in range(40))
        g2 = "\n".join(f"g{i:03d} modified" for i in range(20, 40))
        clear_session(SID)
        try_delta(SID, "git status", g1)
        r7 = try_delta(SID, "git status", g2)
        check("u8-removed-cap", bool(r7) and "(-20)" in r7 and ".. (-5 more in" in r7)

        n1 = "\n".join([f"step {i} ok" for i in range(60)] + [f"FAILED t{i} - e" for i in range(30)])
        clear_session(SID)
        try_delta(SID, "npm test", n1)
        r8 = try_delta(SID, "npm test", n1)
        check("u9-still-cap", bool(r8) and "STILL FAILING / ACTIVE ERRORS (30)" in r8 and r8.count("\n! ") == 25 and ".. (+5 more in" in r8)

        # tiny outputs never bother; clear drops state
        check("u10-short-none", try_delta(SID, "short", "a\nb\nc\nd\ne") is None and try_delta(SID, "short", "a\nb\nc\nd\ne") is None)
        clear_session(SID)
        check("u11-clear", try_delta(SID, "pytest -q", t2) is None)
        check("u12-version", VERSION == "0.10.0")
    finally:
        _unseam(orig, d)

print("=== integration: compress_terminal opt-in ===")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    orig = _seam(d)
    try:
        clear_session("p4-int")
        o1, s1 = compress_terminal("pytest -q", t1, session_id="p4-int", delta_enabled=True)
        check("i1-first-normal", s1.get("gate") == "ok" and not s1.get("delta") and "[tt:terminal" in o1)
        o2, s2 = compress_terminal("pytest -q", t2, session_id="p4-int", delta_enabled=True)
        check("i2-delta", s2.get("delta") is True and o2.startswith("[tt:delta"))
        check("i2-canary", err in o2)
        check("i2-saves", s2.get("saved_chars", 0) == len(t2) - len(o2) and len(o2) < len(t2))
        # determinism contract: delta off (default) -> byte-identical repeats
        a1, _ = compress_terminal("pytest", t1)
        a2, _ = compress_terminal("pytest", t1)
        check("i3-deterministic", a1 == a2)
        b1, sb1 = compress_terminal("pytest -q", t1, session_id="p4-int")
        check("i4-default-off", not sb1.get("delta"))
    finally:
        _unseam(orig, d)

print("=== latency: delta step ===")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    orig = _seam(d)
    try:
        clear_session("p4-lat")
        try_delta("p4-lat", "pytest -q", t1)
        times = []
        for _ in range(7):
            t0 = time.perf_counter()
            try_delta("p4-lat", "pytest -q", t2)
            times.append((time.perf_counter() - t0) * 1000)
        med = sorted(times)[3]
        print(f"  delta-step 200-line median {med:.2f} ms (min {min(times):.2f} max {max(times):.2f}) vs target 1.2 ms")
        check("lat-guard", med < 30.0, f"median={med:.2f}ms")

        big1 = "\n".join(f"test_{i:04d}.py::test_x PASSED" for i in range(2000)) + "\n" + err + "\n===== 1 failed, 2000 passed in 42.1s ====="
        big2 = big1.replace("42.1s", "43.2s")
        clear_session("p4-lat2")
        try_delta("p4-lat2", "pytest -q", big1)
        t0 = time.perf_counter()
        rb = try_delta("p4-lat2", "pytest -q", big2)
        big_ms = (time.perf_counter() - t0) * 1000
        print(f"  delta-step 2000-line {big_ms:.2f} ms (target 1.2 ms) saved={len(big1) - len(rb)} chars")
        check("lat-2000-fires", bool(rb) and err in rb)
    finally:
        _unseam(orig, d)

print(f"\nfails={check.failed}")
sys.exit(1 if check.failed else 0)
