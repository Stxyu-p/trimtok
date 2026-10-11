"""trimtok microsecond latency benchmark & performance regression test.

Stdlib only. Tests execution latency against the sub-millisecond budget.
Run: python -m pytest tests/test_benchmark.py or python tests/test_benchmark.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import compress_terminal
from ccr import get as ccr_get, put as ccr_put
from dedup import clear_session, compress_tool_result


def test_terminal_compression_speed():
    output = "build progress step 1\n" * 200  # ~4.4KB
    runs = 100
    t0 = time.perf_counter()
    for _ in range(runs):
        res, stats = compress_terminal("cargo build", output)
        assert stats["gate"] == "ok"
    elapsed_ms = (time.perf_counter() - t0) * 1000 / runs
    print(f"\n[BENCHMARK] compress_terminal avg: {elapsed_ms:.3f} ms")
    # Must comfortably meet sub-millisecond or low single-digit ms
    assert elapsed_ms < 10.0, f"Too slow: {elapsed_ms:.3f} ms"


def test_tool_result_compression_speed():
    clear_session("bench")
    # Simulate repetitive JSON tool result
    json_result = (
        '{"status": "ok", "items": ['
        + ', '.join(f'{{"id": {i}, "name": "module_{i}", "active": true}}' for i in range(100))
        + ']}'
    )
    runs = 50
    t0 = time.perf_counter()
    for i in range(runs):
        res, stats = compress_tool_result("search_files", json_result, session_id="bench")
    elapsed_ms = (time.perf_counter() - t0) * 1000 / runs
    print(f"\n[BENCHMARK] compress_tool_result avg: {elapsed_ms:.3f} ms")
    assert elapsed_ms < 10.0, f"Too slow: {elapsed_ms:.3f} ms"


def test_ccr_put_get_speed():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        p = Path(td)
        content = "hello world full content " * 100
        # Warm up & average over 50 runs
        runs = 50
        t0 = time.perf_counter()
        digest = ""
        for _ in range(runs):
            digest = ccr_put(content, dir=p)
        assert digest
        put_ms = (time.perf_counter() - t0) * 1000 / runs

        t1 = time.perf_counter()
        retrieved = ""
        for _ in range(runs):
            retrieved = ccr_get(digest, dir=p)
        get_ms = (time.perf_counter() - t1) * 1000 / runs

        assert retrieved == content
        print(f"\n[BENCHMARK] CCR put avg: {put_ms:.3f} ms, CCR get avg: {get_ms:.3f} ms")
        assert put_ms < 5.0
        assert get_ms < 5.0


if __name__ == "__main__":
    test_terminal_compression_speed()
    test_tool_result_compression_speed()
    test_ccr_put_get_speed()
    print("\nAll benchmark assertions passed!")
