"""Fault-signature folding: repeats collapse, differing detail does not."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trimtok.core import _fold_fault_sigs, _fault_sig, compress_terminal
from trimtok.signals import is_important

TB = '  File "C:\\app\\relay.py", line 331, in _start_unmanaged'


def _flagged(lines):
    return [(ln, is_important(ln)) for ln in lines]


def test_repeats_fold_and_keep_first_and_last():
    lines = [
        f"2026-11-08 01:00:0{i},123 WARNING [local-{'ab' * 32}] agent.x: {TB}"
        for i in range(5)
    ]
    out, hidden = _fold_fault_sigs(_flagged(lines))
    text = "\n".join(ln for ln, _ in out)
    assert hidden == 3, hidden
    assert "01:00:00" in text and "01:00:04" in text  # first and last survive
    assert "[template ×3]" in text


def test_differing_detail_is_not_folded_away():
    """`attempt 2/3` and `attempt 3/3` are different facts, not duplicates."""
    base = "2026-11-08 01:00:00,000 WARNING agent.conversation_loop: API call failed (attempt {}/3)"
    lines = [base.format(1), base.format(2), base.format(3)]
    out, hidden = _fold_fault_sigs(_flagged(lines))
    assert hidden == 0, hidden
    assert len(out) == 3


def test_group_below_threshold_is_untouched():
    line = f"2026-11-08 01:00:00,000 ERROR {TB}"
    out, hidden = _fold_fault_sigs(_flagged([line, line]))
    assert hidden == 0
    assert [ln for ln, _ in out] == [line, line]


def test_sig_ignores_only_timestamp_and_session_id():
    assert _fault_sig("2026-11-08 01:00:00,000 ERROR x: retry_after=5") == \
        _fault_sig("2026-11-08 02:00:00,999 ERROR x: retry_after=5")
    assert _fault_sig("ERROR x: retry_after=5") != _fault_sig("ERROR x: retry_after=17")


def test_terminal_compression_still_announces_hidden_lines():
    tb = "\n".join(
        f"2026-11-08 01:00:{i:02d},000 ERROR [local-{'cd' * 32}] agent.tool: Tool x failed: {TB}"
        for i in range(40)
    )
    payload = tb + "\n" + "\n".join(f"line body {i}" for i in range(400))
    out, stats = compress_terminal("pytest", payload, min_chars=0, session_id="t-fault")
    assert stats["gate"] == "ok"
    assert len(out) < len(payload)
    assert "hidden=" in out.split("\n", 1)[0]
