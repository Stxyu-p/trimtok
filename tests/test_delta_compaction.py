"""Regression tests for the v5.8.0 delta compaction guard.

A delta chain is only readable while the base snapshot is still in the
agent's context. Host compaction can drop that base, so a chain longer than
_max_delta_turns is followed by one forced full (base) output that
re-anchors the chain. Reverting the guard turns these tests red.
"""
import os

from trimtok.delta_engine import StatefulDeltaEngine


def _output(seed: int) -> str:
    lines = [f"test_{i:04d}.py::test_x PASSED" for i in range(600)]
    lines.append(f"FAILED test_{seed:04d}.py::test_boom - AssertionError: nope")
    lines.append(f"===== 1 failed, 600 passed in {seed}.8s =====")
    return "\n".join(lines)


def _chain(limit: int | None = None, do_clear: bool = False) -> list[bool]:
    """Run one engine through 6 similar outputs; True = delta delivered."""
    eng = StatefulDeltaEngine()
    old = os.environ.get("TRIMTOK_DELTA_MAX_TURNS")
    if limit is not None:
        os.environ["TRIMTOK_DELTA_MAX_TURNS"] = str(limit)
    try:
        results = []
        for i in range(6):
            out = eng.try_delta("sess", "pytest -q", _output(i))
            results.append(out is not None and out.startswith("[tt:delta"))
            if do_clear and i == 2:
                eng.clear("sess")
        return results
    finally:
        if limit is not None:
            if old is None:
                os.environ.pop("TRIMTOK_DELTA_MAX_TURNS", None)
            else:
                os.environ["TRIMTOK_DELTA_MAX_TURNS"] = old


def test_chain_reanchors_after_limit_deltas():
    # base, 3 deltas, forced base, delta
    assert _chain(3) == [False, True, True, True, False, True]


def test_chain_without_limit_never_resets():
    assert _chain(0) == [False, True, True, True, True, True]


def test_clear_session_resets_the_chain_counter():
    # after clear() the next output is a base, not a delta
    assert _chain(3, do_clear=True)[:4] == [False, True, True, False]


def test_diverged_output_resets_the_chain():
    eng = StatefulDeltaEngine()
    old = os.environ.get("TRIMTOK_DELTA_MAX_TURNS")
    os.environ["TRIMTOK_DELTA_MAX_TURNS"] = "3"
    try:
        # two near-identical runs -> base then delta
        assert eng.try_delta("s2", "pytest -q", _output(1)) is None
        res = eng.try_delta("s2", "pytest -q", _output(2))
        assert res is not None and res.startswith("[tt:delta")
        # a run that diverges sends full output and resets the chain
        diverged = "\n".join(f"line {i} is completely different content here" for i in range(600))
        assert eng.try_delta("s2", "pytest -q", diverged) is None
        # next similar run is again a base, not a delta (chain was reset)
        assert eng.try_delta("s2", "pytest -q", _output(3)) is None
    finally:
        if old is None:
            os.environ.pop("TRIMTOK_DELTA_MAX_TURNS", None)
        else:
            os.environ["TRIMTOK_DELTA_MAX_TURNS"] = old


def test_interleaved_commands_retain_isolated_chain_counters():
    """Interleaved commands in the same session must not wipe out each other's compaction guard."""
    eng = StatefulDeltaEngine()
    old = os.environ.get("TRIMTOK_DELTA_MAX_TURNS")
    os.environ["TRIMTOK_DELTA_MAX_TURNS"] = "3"
    try:
        def _git(seed: int) -> str:
            return "\n".join([f" M file_{i}.py" for i in range(50)] + [f"?? untracked_{seed}.txt"])

        # pytest: base, delta 1
        assert eng.try_delta("s3", "pytest", _output(1)) is None
        assert eng.try_delta("s3", "pytest", _output(2)) is not None
        # git: base
        assert eng.try_delta("s3", "git status", _git(1)) is None
        # pytest: delta 2, delta 3
        assert eng.try_delta("s3", "pytest", _output(3)) is not None
        assert eng.try_delta("s3", "pytest", _output(4)) is not None
        # pytest: 4th run after 3 deltas -> MUST be forced base, NOT delta!
        assert eng.try_delta("s3", "pytest", _output(5)) is None
    finally:
        if old is None:
            os.environ.pop("TRIMTOK_DELTA_MAX_TURNS", None)
        else:
            os.environ["TRIMTOK_DELTA_MAX_TURNS"] = old
