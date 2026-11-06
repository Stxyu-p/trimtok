"""Decision-equivalence quality contract evaluation suite.

Measures whether context compression alters the agent's decision-making, tool
selection, or drops critical identifiers (the E7 cliff-edge prevention).

The gate is strict: a scenario counts as equivalent only when the action is
still visible in the emitted text. A ref that merely points at an original is
reported as recoverable, never as preserved, so a compressor that deletes
everything cannot pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Never write benchmark payloads into the real store or telemetry: that would
# contaminate the honesty-audit baseline.
os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="trimtok-eval-"))

from trimtok import ccr  # noqa: E402

# Standard benchmark scenarios covering common agent tasks. Each context is a
# realistic size: a payload small enough to skip compression would make the
# whole run vacuous.
def _cargo_context() -> str:
    noise = "".join(f"   Compiling dep_{i} v0.{i}.0\n" for i in range(300))
    return (
        noise
        + "error[E0308]: mismatched types\n"
        "  --> src/parser.rs:88:17\n"
        "   |\n"
        "88 |     let x: u32 = parse_name(input);\n"
        "   |            ^^^ expected `u32`, found `String`\n"
    )


def _npm_context() -> str:
    noise = "".join(f"npm http fetch GET 200 https://registry.npmjs.org/pkg-{i} 12ms\n" for i in range(300))
    return (
        noise
        + "npm ERR! code ERESOLVE\n"
        "npm ERR! ERESOLVE unable to resolve dependency tree\n"
        "npm ERR! peer dep missing: react@18\n"
    )


def _db_error_context() -> str:
    frames = "".join(
        f'  File "app/layer_{i}.py", line {100 + i}, in dispatch_{i}\n'
        f"    return handler_{i}(request)\n"
        for i in range(20)
    )
    return (
        "Traceback (most recent call last):\n"
        + frames
        + '  File "db/pool.py", line 88, in acquire_connection\n'
        "    return self._pool.get(timeout=timeout)\n"
        "psycopg2.OperationalError: could not connect to server: Connection refused\n"
        "\tIs the server running on host '127.0.0.1' and accepting\n"
        "\tTCP/IP connections on port 5432?\n"
    )


BUILTIN_SCENARIOS = [
    {
        "id": "cargo-type-error",
        "category": "build_failure",
        "context": _cargo_context(),
        "target_action": "src/parser.rs",
        "critical_tokens": ["E0308", "src/parser.rs", "88"],
    },
    {
        "id": "npm-dependency-conflict",
        "category": "install_failure",
        "context": _npm_context(),
        "target_action": "ERESOLVE",
        "critical_tokens": ["ERESOLVE", "react@18"],
    },
    {
        "id": "database-connection-refused",
        "category": "runtime_error",
        "context": _db_error_context(),
        "target_action": "acquire_connection",
        "critical_tokens": ["5432", "127.0.0.1", "db/pool.py", "88"],
    },
]


def _extract_refs(text: str) -> list[str]:
    """Extract CCR aliases from text."""
    matches = re.findall(r"#(c[0-9A-Za-z]+)", text)
    return [f"#{m}" for m in matches]


def _visible(needle: str, haystack: str) -> bool:
    """Whole-token containment: '88' must not match inside '188'."""
    if not needle:
        return False
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def _recoverable(needle: str, compressed: str) -> bool:
    for ref in _extract_refs(compressed):
        original = ccr.get(ref)
        if original and needle in original:
            return True
    return False


def evaluate_decision_equivalence(
    scenarios: list[dict[str, Any]],
    compress_fn: Callable[[str], str],
) -> dict[str, Any]:
    """Evaluate decision preservation across paired scenarios."""
    total = len(scenarios)
    if total == 0:
        return {
            "valid": False,
            "total": 0,
            "equivalent": 0,
            "recoverable_only": 0,
            "decision_change_rate": 0.0,
            "decision_recoverable_rate": 0.0,
            "critical_tokens_total": 0,
            "critical_tokens_preserved": 0,
            "critical_tokens_preserved_rate": 0.0,
            "char_reduction_rate": 0.0,
            "results": [],
        }

    equivalent_count = 0
    recoverable_only_count = 0
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

        action_visible = _visible(target_action, compressed)
        action_recoverable = (not action_visible) and _recoverable(target_action, compressed)

        if action_visible:
            equivalent_count += 1
        elif action_recoverable:
            recoverable_only_count += 1

        # Check critical tokens
        scenario_crit_total = len(critical_tokens)
        scenario_crit_preserved = 0
        for tok in critical_tokens:
            if _visible(tok, compressed) or _recoverable(tok, compressed):
                scenario_crit_preserved += 1

        total_critical += scenario_crit_total
        preserved_critical += scenario_crit_preserved

        scenario_results.append({
            "id": sid,
            "orig_chars": orig_chars,
            "comp_chars": comp_chars,
            "reduction_pct": round((1.0 - comp_chars / max(1, orig_chars)) * 100, 2),
            "compressed": comp_chars < orig_chars,
            "equivalent": action_visible,
            "action_visible": action_visible,
            "action_recoverable_only": action_recoverable,
            "critical_preserved": scenario_crit_preserved,
            "critical_total": scenario_crit_total,
        })

    reduction_rate = (orig_chars_sum - comp_chars_sum) / max(1, orig_chars_sum)

    return {
        # A scenario that was not compressed proves nothing about preserving a
        # decision under compression, so the run is invalid unless every
        # scenario actually shrank.
        "valid": reduction_rate > 0.0 and all(r["compressed"] for r in scenario_results),
        "total": total,
        "equivalent": equivalent_count,
        "recoverable_only": recoverable_only_count,
        "decision_change_rate": round((total - equivalent_count) / total, 4),
        "decision_recoverable_rate": round((total - recoverable_only_count) / total, 4),
        "critical_tokens_total": total_critical,
        "critical_tokens_preserved": preserved_critical,
        "critical_tokens_preserved_rate": round(preserved_critical / max(1, total_critical), 4),
        "char_reduction_rate": round(reduction_rate, 4),
        "results": scenario_results,
    }


def run_benchmark() -> int:
    parser = argparse.ArgumentParser(description="Run Decision-Equivalence Quality Benchmark")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    from trimtok.core import compress_terminal

    def default_compressor(text: str) -> str:
        out, _ = compress_terminal("", text, min_chars=128)
        return out

    metrics = evaluate_decision_equivalence(BUILTIN_SCENARIOS, default_compressor)
    passed = metrics["valid"] and metrics["decision_change_rate"] == 0.0

    if args.json:
        print(json.dumps(metrics, indent=2))
        return 0 if passed else 1

    print("================================================================================")
    print(" 🎯 TRIMTOK DECISION-EQUIVALENCE QUALITY CONTRACT BENCHMARK")
    print("================================================================================")
    print(f"Total Scenarios Tested : {metrics['total']}")
    print(f"Decision Visible       : {metrics['equivalent']} / {metrics['total']} ({round((1 - metrics['decision_change_rate']) * 100, 1)}%)")
    print(f"Recoverable Only       : {metrics['recoverable_only']} (ref-only, not counted as preserved)")
    print(f"Critical Tokens Saved  : {metrics['critical_tokens_preserved']} / {metrics['critical_tokens_total']} ({round(metrics['critical_tokens_preserved_rate'] * 100, 1)}%)")
    print(f"Mean Context Reduction : {round(metrics['char_reduction_rate'] * 100, 1)}%")
    print("--------------------------------------------------------------------------------")
    for r in metrics["results"]:
        status = "✅ PASS" if r["equivalent"] else "❌ FAIL"
        state = "Retained" if r["action_visible"] else ("Recoverable" if r["action_recoverable_only"] else "Lost")
        print(f"[{status}] {r['id']:<30} State: {state:<12} Tokens: {r['critical_preserved']}/{r['critical_total']} (-{r['reduction_pct']}%)")
    print("================================================================================")
    if not metrics["valid"]:
        print("❌ INVALID: the compressor removed nothing, so this run proves nothing.")
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(run_benchmark())
