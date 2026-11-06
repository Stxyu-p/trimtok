"""Decision-equivalence quality contract evaluation suite.

Measures whether context compression alters the agent's decision-making,
tool selection, or drops critical identifiers (the E7 cliff-edge prevention).
Inspired by Distil's evaluation contract and three-state information model.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trimtok import ccr

# Standard benchmark scenarios covering common agent tasks
BUILTIN_SCENARIOS = [
    {
        "id": "pytest-assertion-failure",
        "category": "test_failure",
        "context": (
            "================================== FAILURES ===================================\n"
            "_____________________________ test_auth_token_expiry __________________________\n"
            "tests/test_auth.py:42: in test_auth_token_expiry\n"
            "    assert token.is_valid() is True\n"
            "E   AssertionError: assert False is True\n"
            "E    + where False = is_valid()\n"
            "=========================== short test summary info ===========================\n"
            "FAILED tests/test_auth.py::test_auth_token_expiry - AssertionError: assert False is True\n"
            "============================== 1 failed in 0.45s ==============================\n"
        ),
        "target_action": "test_auth_token_expiry",
        "critical_tokens": ["tests/test_auth.py", "42", "AssertionError"],
    },
    {
        "id": "git-diff-conflict",
        "category": "merge_conflict",
        "context": (
            "<<<<<<< HEAD\n"
            "def calculate_tax(amount: float) -> float:\n"
            "    return amount * 0.07\n"
            "=======\n"
            "def calculate_tax(amount: float) -> float:\n"
            "    return round(amount * 0.07, 2)\n"
            ">>>>>>> feature/tax-rounding\n"
        ),
        "target_action": "calculate_tax",
        "critical_tokens": ["calculate_tax", "feature/tax-rounding", "HEAD"],
    },
    {
        "id": "database-connection-refused",
        "category": "runtime_error",
        "context": (
            "psycopg2.OperationalError: could not connect to server: Connection refused\n"
            "\tIs the server running on host '127.0.0.1' and accepting\n"
            "\tTCP/IP connections on port 5432?\n"
            "Error in db/pool.py line 88 in acquire_connection\n"
        ),
        "target_action": "acquire_connection",
        "critical_tokens": ["5432", "127.0.0.1", "db/pool.py", "88"],
    },
]


def _extract_refs(text: str) -> list[str]:
    """Extract CCR aliases or digests from text."""
    matches = re.findall(r"#(c[0-9A-Za-z]+)", text)
    return [f"#{m}" for m in matches]


def evaluate_decision_equivalence(
    scenarios: list[dict[str, Any]],
    compress_fn: Callable[[str], str],
) -> dict[str, Any]:
    """Evaluate decision preservation across paired scenarios."""
    total = len(scenarios)
    if total == 0:
        return {
            "total": 0,
            "equivalent": 0,
            "decision_change_rate": 0.0,
            "critical_tokens_total": 0,
            "critical_tokens_preserved": 0,
            "critical_tokens_preserved_rate": 1.0,
            "char_reduction_rate": 0.0,
            "results": [],
        }

    equivalent_count = 0
    total_critical = 0
    preserved_critical = 0
    orig_chars_sum = 0
    comp_chars_sum = 0
    scenario_results: list[dict[str, Any]] = []

    for s in scenarios:
        sid = s.get("id", "unknown")
        context = s.get("context", "")
        target_action = s.get("target_action", "")
        critical_tokens = s.get("critical_tokens", [])

        orig_chars = len(context)
        orig_chars_sum += orig_chars

        compressed = compress_fn(context)
        comp_chars = len(compressed)
        comp_chars_sum += comp_chars

        # Check action visibility
        action_retained = bool(target_action and target_action in compressed)
        action_recoverable = False

        if not action_retained and target_action:
            refs = _extract_refs(compressed)
            for ref in refs:
                original = ccr.get(ref)
                if original and target_action in original:
                    action_recoverable = True
                    break

        is_equivalent = action_retained or action_recoverable
        if is_equivalent:
            equivalent_count += 1

        # Check critical tokens
        scenario_crit_total = len(critical_tokens)
        scenario_crit_preserved = 0
        for tok in critical_tokens:
            if tok in compressed:
                scenario_crit_preserved += 1
            else:
                refs = _extract_refs(compressed)
                for ref in refs:
                    original = ccr.get(ref)
                    if original and tok in original:
                        scenario_crit_preserved += 1
                        break

        total_critical += scenario_crit_total
        preserved_critical += scenario_crit_preserved

        scenario_results.append({
            "id": sid,
            "orig_chars": orig_chars,
            "comp_chars": comp_chars,
            "reduction_pct": round((1.0 - comp_chars / max(1, orig_chars)) * 100, 2),
            "equivalent": is_equivalent,
            "action_retained": action_retained,
            "action_recoverable": action_recoverable,
            "critical_preserved": scenario_crit_preserved,
            "critical_total": scenario_crit_total,
        })

    change_rate = (total - equivalent_count) / total
    crit_rate = preserved_critical / max(1, total_critical)
    reduction_rate = (orig_chars_sum - comp_chars_sum) / max(1, orig_chars_sum)

    return {
        "total": total,
        "equivalent": equivalent_count,
        "decision_change_rate": round(change_rate, 4),
        "critical_tokens_total": total_critical,
        "critical_tokens_preserved": preserved_critical,
        "critical_tokens_preserved_rate": round(crit_rate, 4),
        "char_reduction_rate": round(reduction_rate, 4),
        "results": scenario_results,
    }


def run_benchmark() -> int:
    parser = argparse.ArgumentParser(description="Run Decision-Equivalence Quality Benchmark")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    from trimtok.core import compress_terminal

    def default_compressor(text: str) -> str:
        out, _ = compress_terminal("test", text, min_chars=128)
        return out

    metrics = evaluate_decision_equivalence(BUILTIN_SCENARIOS, default_compressor)

    if args.json:
        print(json.dumps(metrics, indent=2))
        return 0

    print("================================================================================")
    print(" 🎯 TRIMTOK DECISION-EQUIVALENCE QUALITY CONTRACT BENCHMARK")
    print("================================================================================")
    print(f"Total Scenarios Tested : {metrics['total']}")
    print(f"Decision Preserved     : {metrics['equivalent']} / {metrics['total']} ({round((1 - metrics['decision_change_rate']) * 100, 1)}%)")
    print(f"Critical Tokens Saved  : {metrics['critical_tokens_preserved']} / {metrics['critical_tokens_total']} ({round(metrics['critical_tokens_preserved_rate'] * 100, 1)}%)")
    print(f"Mean Context Reduction : {round(metrics['char_reduction_rate'] * 100, 1)}%")
    print("--------------------------------------------------------------------------------")
    for r in metrics["results"]:
        status = "✅ PASS" if r["equivalent"] else "❌ FAIL"
        state = "Retained" if r["action_retained"] else ("Recoverable" if r["action_recoverable"] else "Lost")
        print(f"[{status}] {r['id']:<30} State: {state:<12} Tokens: {r['critical_preserved']}/{r['critical_total']} (-{r['reduction_pct']}%)")
    print("================================================================================")
    return 0 if metrics["decision_change_rate"] == 0.0 else 1


if __name__ == "__main__":
    sys.exit(run_benchmark())
