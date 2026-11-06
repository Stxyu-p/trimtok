from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.decision_eval import evaluate_decision_equivalence
from trimtok import ccr

def test_decision_equivalence_deterministic_scenarios():
    scenarios = [
        {
            "id": "scenario-error-auth",
            "context": "AssertionError: TokenExpiredException on line 42 in auth.py. Run fix_auth.",
            "target_action": "fix_auth",
            "critical_tokens": ["auth.py", "42", "TokenExpiredException"],
        },
        {
            "id": "scenario-db-fail",
            "context": "FATAL: connection to server on port 5432 failed: ConnectionRefused. Run restart_db.",
            "target_action": "restart_db",
            "critical_tokens": ["5432", "ConnectionRefused"],
        }
    ]
    
    # 1. Identity / Pass-through compressor
    res = evaluate_decision_equivalence(scenarios, compress_fn=lambda x: x)
    assert res["total"] == 2
    assert res["equivalent"] == 2
    assert res["decision_change_rate"] == 0.0
    assert res["critical_tokens_preserved_rate"] == 1.0
    # A compressor that removes nothing cannot prove anything.
    assert res["valid"] is False

def test_decision_equivalence_catches_divergence():
    scenarios = [
        {
            "id": "scenario-critical-loss",
            "context": "CRITICAL: memory leak detected in cache.py line 99. Run prune_cache.",
            "target_action": "prune_cache",
            "critical_tokens": ["cache.py", "line 99"],
        }
    ]
    
    # Destructive compressor that wipes everything
    def bad_compressor(text: str) -> str:
        return "Everything looks fine, no errors."

    res = evaluate_decision_equivalence(scenarios, compress_fn=bad_compressor)
    assert res["total"] == 1
    assert res["equivalent"] == 0
    assert res["decision_change_rate"] == 1.0
    assert res["critical_tokens_preserved_rate"] == 0.0

def test_ref_only_compression_is_not_counted_as_preserved():
    """A compressor that emits only a ref must not pass the gate."""
    context = "CRITICAL: memory leak detected in cache.py line 99. Run prune_cache."
    scenarios = [{
        "id": "ref-only",
        "context": context,
        "target_action": "prune_cache",
        "critical_tokens": ["cache.py"],
    }]

    def ref_only(_text: str) -> str:
        return f"[tt:folded 40L {ccr.put(context)}]"

    res = evaluate_decision_equivalence(scenarios, compress_fn=ref_only)
    assert res["equivalent"] == 0
    assert res["recoverable_only"] == 1
    assert res["decision_change_rate"] == 1.0
