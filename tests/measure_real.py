"""Real token-reduction + latency + accuracy measurement per trimtok function.

Stdlib only. Run: python tests/measure_real.py
Reports, per function, median latency (ms), char reduction %, token reduction %,
and an accuracy check (errors / important lines survive compression).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import compress_terminal
from dedup import compress_tool_result, clear_session
from trimtok.signals import estimate_tokens
from ccr import put as ccr_put, get as ccr_get, purge as ccr_purge

RUNS = 30
WARMUP = 5


def _latency(fn, *a, **k):
    result = None
    for _ in range(WARMUP):
        result = fn(*a, **k)
    samples = []
    for _ in range(RUNS):
        t0 = time.perf_counter_ns()
        result = fn(*a, **k)
        samples.append((time.perf_counter_ns() - t0) / 1_000_000)
    return statistics.median(samples), result


def _report(name, before, after, stats, lat, acc_ok, acc_note=""):
    before_tok = estimate_tokens(before)
    after_tok = estimate_tokens(after)
    char_pct = (1 - len(after) / len(before)) * 100 if before else 0
    tok_pct = (1 - after_tok / before_tok) * 100 if before_tok else 0
    flag = "OK " if acc_ok else "ACC!"
    print(
        f"[{flag}] {name:34s} lat={lat:6.3f}ms  "
        f"chars {char_pct:6.1f}%  tokens {tok_pct:6.1f}%  "
        f"(tok {before_tok}->{after_tok})  gate={stats.get('gate')}"
        + (f"  {acc_note}" if acc_note else "")
    )


def _error_log(n=400):
    lines = []
    for i in range(n):
        lines.append(f"2026-10-24 10:00:{i:03d} INFO processing request id={i}")
        if i % 20 == 0:
            lines.append(f"ERROR test_{i}.py::test_case {i}: AssertionError expected 1 got 2")
    return "\n".join(lines)


def _diff(n=200):
    out = ["diff --git a/file.py b/file.py", "--- a/file.py", "+++ b/file.py"]
    for i in range(n):
        out.append("@@ -1,1 +1,1 @@")
        out.append(f"-old line {i}")
        out.append(f"+new line {i}")
    return "\n".join(out)


def _tool_json(n=100):
    return json.dumps(
        {"run": "r1", "items": [{"id": i, "name": f"mod_{i}", "active": True} for i in range(n)]},
        indent=2,
    )


def main():
    # 1. terminal: error log (repeated, high templating)
    text = _error_log()
    lat, (out, st) = _latency(compress_terminal, "cat log.txt", text, min_chars=200)
    acc = "ERROR" in out
    _report("terminal: error log", text, out, st, lat, acc,
            "errors kept" if acc else "ERROR LOST")

    # 2. terminal: diff
    text = _diff()
    lat, (out, st) = _latency(compress_terminal, "git diff", text, min_chars=200)
    acc = "diff --git" in out
    _report("terminal: diff", text, out, st, lat, acc)

    # 3. terminal: small (<512) passthrough
    text = "short output line\n" * 3
    lat, (out, st) = _latency(compress_terminal, "echo hi", text, min_chars=200)
    _report("terminal: small passthrough", text, out, st, lat, True)

    # 4. tool result: templated JSON (TOON)
    text = _tool_json()
    clear_session("m:1")
    lat, (out, st) = _latency(compress_tool_result, "search_files", text, session_id="m:1")
    acc = (ccr_get(st.get("digest", "")) == text) if st.get("digest") else False
    _report("tool: templated json (TOON)", text, out, st, lat, acc,
            "retrievable" if acc else "NOT RETRIEVABLE")

    # 5. tool result: cross-turn fold
    clear_session("m:2")
    seed = _tool_json(100)
    compress_tool_result("search_files", seed, session_id="m:2")
    query = json.dumps({"run": "r2", "items": [{"id": i, "name": f"mod_{i}", "active": True} for i in range(100)]}, indent=2)
    lat, (out, st) = _latency(compress_tool_result, "search_files", query, session_id="m:2")
    acc = (ccr_get(st.get("digest", "")) == query) if st.get("digest") else False
    _report("tool: cross-turn fold", query, out, st, lat, acc,
            "fold_saved=%d retrievable=%s" % (st.get("fold_saved", 0), acc))

    # 6. tool result: unique non-templated (should revert, no loss)
    uniq = "\n".join(f"record_{i:04d}_value_{i*37:08d}_hash_{(i*i)%1000003:07d}_tag_{i%26}_kqj_" for i in range(200))
    clear_session("m:3")
    lat, (out, st) = _latency(compress_tool_result, "search_files", uniq, session_id="m:3")
    acc = (ccr_get(st.get("digest", "")) == uniq) if st.get("digest") else False
    _report("tool: unique non-templated", uniq, out, st, lat, acc,
            "retrievable" if acc else "NOT RETRIEVABLE")

    # 7. CCR put/get latency
    ccr_purge()
    payload = "x" * 2000
    lat, ref = _latency(ccr_put, payload)
    lat2, got = _latency(ccr_get, ref)
    acc = (got == payload)
    _report("CCR put", payload, got or "", {"gate": "put"}, lat, acc,
            "get lat=%.3fms ref=%s" % (lat2, ref))


if __name__ == "__main__":
    main()
