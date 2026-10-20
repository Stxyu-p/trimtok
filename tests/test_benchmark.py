"""Synthetic latency regression checks with stateful paths measured separately.

Stdlib only. Run: python -m pytest tests/test_benchmark.py or
python tests/test_benchmark.py
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import compress_terminal
from ccr import get as ccr_get, put as ccr_put
from dedup import (
    clear_session,
    compress_tool_result,
    expire_old_turns,
    note_turn,
)


RUNS = 50
WARMUP_RUNS = 5
LATENCY_BUDGET_MS = 10.0


def _timed_call(operation):
    started = time.perf_counter_ns()
    result = operation()
    return (time.perf_counter_ns() - started) / 1_000_000, result


def _summarize(name: str, samples: list[float], budget_ms: float) -> None:
    ordered = sorted(samples)
    median_ms = statistics.median(ordered)
    p95_ms = ordered[math.ceil(0.95 * len(ordered)) - 1]
    print(
        f"\n[BENCHMARK] {name}: median={median_ms:.3f} ms, "
        f"p95={p95_ms:.3f} ms (n={len(ordered)})"
    )
    # Median is the regression gate; p95 is reported to expose tail latency
    # without making this synthetic check overly sensitive to scheduler noise.
    assert median_ms < budget_ms, (
        f"{name} median exceeded {budget_ms:.1f} ms: {median_ms:.3f} ms"
    )


def _measure_case(name: str, run_once, expected_gate: str, budget_ms: float) -> None:
    for index in range(WARMUP_RUNS):
        _, stats = run_once(index)
        assert stats["gate"] == expected_gate, (
            f"{name} warm-up expected {expected_gate}, got {stats['gate']}"
        )

    samples = []
    for index in range(WARMUP_RUNS, WARMUP_RUNS + RUNS):
        elapsed_ms, stats = run_once(index)
        assert stats["gate"] == expected_gate, (
            f"{name} expected {expected_gate}, got {stats['gate']}"
        )
        samples.append(elapsed_ms)
    _summarize(name, samples, budget_ms)


def _tool_json_result(run_id: str) -> str:
    return json.dumps(
        {
            "run": run_id,
            "status": "ok",
            "items": [
                {"id": index, "name": f"module_{index}", "active": True}
                for index in range(100)
            ],
        },
        indent=2,
    )


def _first_seen_sample(index: int):
    session_id = f"benchmark:first-seen:{index}"
    clear_session(session_id)
    result = _tool_json_result(str(index))
    try:
        elapsed_ms, (output, stats) = _timed_call(
            lambda: compress_tool_result(
                "search_files", result, session_id=session_id
            )
        )
        assert output != result and stats.get("saved_chars", 0) > 0
        return elapsed_ms, stats
    finally:
        clear_session(session_id)


def _repeat_passthrough_sample(index: int):
    session_id = f"benchmark:repeat:{index}"
    clear_session(session_id)
    result = _tool_json_result("repeat")
    try:
        _, first_stats = compress_tool_result(
            "search_files", result, session_id=session_id
        )
        assert first_stats["gate"] == "ok"
        elapsed_ms, (output, stats) = _timed_call(
            lambda: compress_tool_result(
                "search_files", result, session_id=session_id
            )
        )
        # v4.2 contract change: an exact within-turn repeat is re-compressed
        # (TOON / span fold) instead of being passed through raw. The old
        # passthrough re-sent the whole payload -- ~1.3K tokens the agent had
        # already paid for. The contract is now "smaller, never larger".
        assert stats["gate"] == "ok", stats
        assert len(output) < len(result), (len(output), len(result))
        return elapsed_ms, stats
    finally:
        clear_session(session_id)


def _volatile_result(offset: int) -> str:
    return "\n".join(
        f"entry_{index:03d} id "
        f"00000000-0000-4000-8000-{index + offset:012x} "
        "carries stable content payload for cross-turn benchmark"
        for index in range(60)
    )


def _cross_turn_fold_sample(index: int):
    session_id = f"benchmark:cross-turn:{index}"
    clear_session(session_id)
    seed = _volatile_result(index * 100)
    query = _volatile_result(100_000 + index * 100)
    try:
        _, seed_stats = compress_tool_result(
            "search_files", seed, session_id=session_id
        )
        assert seed_stats["gate"] == "ok" and seed_stats.get("digest")

        # Match the hook lifecycle: a successful prior-turn result is indexed,
        # then the turn clock advances before the next result is measured.
        note_turn(seed, seed_stats["digest"], session_id, "turn-1")
        expire_old_turns(session_id, keep_turns=2)

        elapsed_ms, (output, stats) = _timed_call(
            lambda: compress_tool_result(
                "search_files", query, session_id=session_id
            )
        )
        assert output != query and stats.get("fold_saved", 0) > 0
        note_turn(query, stats.get("digest", ""), session_id, "turn-2")
        return elapsed_ms, stats
    finally:
        clear_session(session_id)


def _idx_letters(index: int) -> str:
    # ponytail: base-26 letters break Drain template keys (digits fold to
    # slots, letters stay) so each line is its own group of 1.
    chars = []
    value = index
    for _ in range(4):
        chars.append(chr(ord('a') + value % 26))
        value //= 26
    return ''.join(chars)


def _large_no_gain_result(run_id: int) -> str:
    # Case A fixture: strictly unique non-templated lines. Per-line letter
    # tags keep Drain groups <3 so the token-gate reverts (reverted_gate).
    # Stale-assertion root cause was _fold_intra_templates, not repeats.
    return '\n'.join(
        f"record_{run_id:03d}_{index:04d}_value_{index * 37:08d}_"
        f"hash_{(index * index) % 1_000_003:07d}_tag_{_idx_letters(index)}_kqj_"
        f"state_{(index * 7_919) % 999_983:06d}_{_idx_letters(index * 13 + 7)}"
        for index in range(180)
    )


def _no_gain_sample(index: int):
    session_id = f"benchmark:no-gain:{index}"
    clear_session(session_id)
    result = _large_no_gain_result(index)
    try:
        elapsed_ms, (output, stats) = _timed_call(
            lambda: compress_tool_result(
                "search_files", result, session_id=session_id
            )
        )
        assert output == result
        return elapsed_ms, stats
    finally:
        clear_session(session_id)


def test_terminal_compression_speed():
    output = "build progress step 1\n" * 200  # ~4.4KB

    def run_once(_index: int):
        elapsed_ms, (result, stats) = _timed_call(
            lambda: compress_terminal("cargo build", output)
        )
        assert isinstance(result, str)
        return elapsed_ms, stats

    _measure_case(
        "compress_terminal (4.4 KB repeated log)",
        run_once,
        expected_gate="ok",
        budget_ms=LATENCY_BUDGET_MS,
    )


def test_tool_result_compression_scenarios():
    # First-seen pretty JSON now includes the default-on TOON fold (parse +
    # round-trip verify ≈ +3-6 ms quiet, more under load); budget it above the
    # plain-fold cases on purpose while staying far below the 50 ms guard.
    _measure_case(
        "compress_tool_result first-seen / fresh session (pretty JSON)",
        _first_seen_sample,
        expected_gate="ok",
        budget_ms=25.0,
    )
    # v4.2: an exact repeat is re-compressed (TOON/span fold) instead of being
    # passed through raw, so it costs the same as a first-seen fold -- budget it
    # like one. The old passthrough re-sent the whole payload (~1.3K tokens).
    _measure_case(
        "compress_tool_result exact-repeat (recompressed, v4.2)",
        _repeat_passthrough_sample,
        expected_gate="ok",
        budget_ms=25.0,
    )
    _measure_case(
        "compress_tool_result cross-turn fold (60 shared lines)",
        _cross_turn_fold_sample,
        expected_gate="ok",
        budget_ms=LATENCY_BUDGET_MS,
    )
    _measure_case(
        "compress_tool_result large no-gain input (~9.5 KB)",
        _no_gain_sample,
        expected_gate="reverted_gate",
        budget_ms=LATENCY_BUDGET_MS,
    )


def test_ccr_put_get_speed():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        contents = {}
        put_samples = []
        digests = []

        for index in range(WARMUP_RUNS + RUNS):
            content = f"hello world full content run={index} " * 100
            elapsed_ms, digest = _timed_call(
                lambda content=content: ccr_put(content, dir=directory)
            )
            assert digest
            contents[digest] = content
            digests.append(digest)
            if index >= WARMUP_RUNS:
                put_samples.append(elapsed_ms)

        # Keep read measurements on one existing entry, matching the previous
        # warm local-cache workload; unique-file reads have different disk costs.
        read_digest = digests[-1]
        for _ in range(WARMUP_RUNS):
            assert ccr_get(read_digest, dir=directory) == contents[read_digest]

        get_samples = []
        for _ in range(RUNS):
            elapsed_ms, retrieved = _timed_call(
                lambda: ccr_get(read_digest, dir=directory)
            )
            assert retrieved == contents[read_digest]
            get_samples.append(elapsed_ms)

        _summarize("CCR put (unique payload)", put_samples, budget_ms=5.0)
        _summarize(
            "CCR get (same stored payload, repeated)",
            get_samples,
            budget_ms=5.0,
        )


def test_ccr_v2_put_get_speed():
    import tempfile
    from pathlib import Path

    import ccr_v2

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        contents = {}
        refs = []
        put_samples = []

        for index in range(WARMUP_RUNS + RUNS):
            content = f"v2 payload run={index} " * 100
            elapsed_ms, ref = _timed_call(
                lambda content=content: ccr_v2.put(content, dir=directory)
            )
            assert ref.startswith("#") and len(ref) <= 5
            contents[ref] = content
            refs.append(ref)
            if index >= WARMUP_RUNS:
                put_samples.append(elapsed_ms)

        read_ref = refs[-1]
        for _ in range(WARMUP_RUNS):
            assert ccr_v2.get(read_ref, dir=directory) == contents[read_ref]

        get_samples = []
        for _ in range(RUNS):
            elapsed_ms, retrieved = _timed_call(
                lambda: ccr_v2.get(read_ref, dir=directory)
            )
            assert retrieved == contents[read_ref]
            get_samples.append(elapsed_ms)

        _summarize("CCR v2 put (unique payload)", put_samples, budget_ms=3.0)
        _summarize("CCR v2 get (same alias, cached)", get_samples, budget_ms=1.0)
        ccr_v2.close(dir=directory)


if __name__ == "__main__":
    test_terminal_compression_speed()
    test_tool_result_compression_scenarios()
    test_ccr_put_get_speed()
    test_ccr_v2_put_get_speed()
    print("\nAll benchmark assertions passed!")
