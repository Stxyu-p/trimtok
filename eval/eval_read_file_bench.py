"""Multi-approach read_file benchmark runner and metric evaluator.

Compares strategies across:
1. Strategy A: RTK / Repomix Pure Signature Stripping
2. Strategy B: Squeez / Lossless Cosmetic Minification
3. Strategy C: Tiered Semantic Shaving with Line Anchors (Proposed)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

# Ensure trimtok is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trimtok.strategies.rtk_strip import strip_signatures_rtk
from trimtok.strategies.lossless_min import minify_lossless
from trimtok.strategies.tiered_shave import shave_tiered

_LINE_NO_RE = re.compile(r"^(\d+)\|(.*)$")

@dataclass
class BenchmarkMetric:
    before_chars: int
    after_chars: int
    latency_ms: float
    patch_safe: bool
    kept_functions: int = 0
    folded_functions: int = 0
    gate: str = "ok"

    @property
    def saved_chars(self) -> int:
        return max(0, self.before_chars - self.after_chars)

    @property
    def reduction_pct(self) -> float:
        if self.before_chars <= 0:
            return 0.0
        return (self.saved_chars / self.before_chars) * 100.0


def verify_patch_safety(original: str, transformed: str) -> bool:
    orig_lines = original.split("\n")
    orig_map: dict[int, str] = {}
    is_numbered = False
    for line in orig_lines:
        m = _LINE_NO_RE.match(line)
        if m:
            is_numbered = True
            orig_map[int(m.group(1))] = m.group(2)

    if not is_numbered:
        return True

    trans_lines = transformed.split("\n")
    checked = 0
    for line in trans_lines:
        s = line.strip()
        if not s or s.startswith(("[tt:", "---", "/*", "//", "Outline", "@")):
            continue
        m = _LINE_NO_RE.match(line)
        if not m:
            return False
        line_no = int(m.group(1))
        content = m.group(2)
        if content.strip().startswith(('[tt:', '---', '/*', '//', 'Outline', '@')):
            continue
        if line_no in orig_map:
            if orig_map[line_no].strip() != content.strip() and not content.strip().startswith("..."):
                return False
            checked += 1

    return checked > 0 or len(trans_lines) < 5


def evaluate_strategy_on_payload(
    strategy_fn: Callable[[str, dict], tuple[str, dict]],
    payload: dict[str, Any],
) -> BenchmarkMetric:
    content = payload.get("content", "")
    t0 = time.perf_counter()
    transformed, stats = strategy_fn(content, payload)
    dt = (time.perf_counter() - t0) * 1000.0

    patch_safe = verify_patch_safety(content, transformed)
    kept = stats.get("kept_functions", 0)
    folded = stats.get("folded_functions", 0)
    gate = stats.get("gate", "ok")

    return BenchmarkMetric(
        before_chars=len(content),
        after_chars=len(transformed),
        latency_ms=dt,
        patch_safe=patch_safe,
        kept_functions=kept,
        folded_functions=folded,
        gate=gate,
    )


def load_corpus(corpus_dir: Path | None = None) -> list[dict[str, Any]]:
    target = corpus_dir or (Path(__file__).resolve().parent / "corpus_read" / "corpus")
    payloads = []
    for f in sorted(target.glob("*.json")):
        payloads.append(json.loads(f.read_text(encoding="utf-8")))
    return payloads


def run_full_benchmark() -> dict[str, Any]:
    corpus = load_corpus()
    strategies = {
        "Strategy A (RTK / Repomix Signature Strip)": strip_signatures_rtk,
        "Strategy B (Squeez / Lossless Minification)": minify_lossless,
        "Strategy C (Tiered Semantic Shaving - Ours)": shave_tiered,
    }

    results: dict[str, Any] = {}
    for strat_name, strat_fn in strategies.items():
        domain_stats: dict[str, dict[str, Any]] = {}
        total_before = 0
        total_after = 0
        total_ms = 0.0
        all_patch_safe = True
        total_kept_fn = 0
        total_folded_fn = 0
        payload_details = []

        for p in corpus:
            dom = p.get("domain", "misc")
            if dom not in domain_stats:
                domain_stats[dom] = {"before": 0, "after": 0, "ms": 0.0, "safe": True}

            m = evaluate_strategy_on_payload(strat_fn, p)
            domain_stats[dom]["before"] += m.before_chars
            domain_stats[dom]["after"] += m.after_chars
            domain_stats[dom]["ms"] += m.latency_ms
            if not m.patch_safe:
                domain_stats[dom]["safe"] = False
                all_patch_safe = False

            total_before += m.before_chars
            total_after += m.after_chars
            total_ms += m.latency_ms
            total_kept_fn += m.kept_functions
            total_folded_fn += m.folded_functions

            payload_details.append({
                "id": p["id"],
                "domain": dom,
                "before": m.before_chars,
                "after": m.after_chars,
                "reduction_pct": round(m.reduction_pct, 2),
                "ms": round(m.latency_ms, 2),
                "patch_safe": m.patch_safe,
                "kept_fn": m.kept_functions,
                "folded_fn": m.folded_functions,
            })

        overall_saved = max(0, total_before - total_after)
        overall_reduction = (overall_saved / total_before * 100.0) if total_before > 0 else 0.0

        domain_summary = {}
        for dom, ds in domain_stats.items():
            d_saved = max(0, ds["before"] - ds["after"])
            domain_summary[dom] = {
                "before_chars": ds["before"],
                "after_chars": ds["after"],
                "reduction_pct": round((d_saved / ds["before"] * 100.0) if ds["before"] > 0 else 0.0, 2),
                "avg_ms": round(ds["ms"] / 4.0, 2),
                "patch_safe": ds["safe"],
            }

        results[strat_name] = {
            "total_before": total_before,
            "total_after": total_after,
            "overall_saved": overall_saved,
            "overall_reduction_pct": round(overall_reduction, 2),
            "total_ms": round(total_ms, 2),
            "avg_ms_per_payload": round(total_ms / len(corpus), 2),
            "all_patch_safe": all_patch_safe,
            "total_kept_functions": total_kept_fn,
            "total_folded_functions": total_folded_fn,
            "domain_summary": domain_summary,
            "payloads": payload_details,
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="Multi-approach read_file benchmark")
    parser.add_argument("--json", action="store_true", help="output json results")
    args = parser.parse_args()

    results = run_full_benchmark()
    if args.json:
        print(json.dumps(results, indent=2))
        return

    print("=" * 80)
    print("       READ_FILE COMPRESSION STRATEGY COMPARATIVE BENCHMARK (16 REAL PAYLOADS)")
    print("=" * 80)
    for strat, data in results.items():
        print(f"\n>>> {strat}")
        print(f"  Overall Reduction: {data['overall_reduction_pct']}%  ({data['overall_saved']:,} / {data['total_before']:,} chars)")
        print(f"  Avg Latency:       {data['avg_ms_per_payload']} ms")
        print(f"  Patch Safe:        {'YES (100%)' if data['all_patch_safe'] else 'FAILED'}")
        print(f"  Functions:         Kept={data['total_kept_functions']} | Folded={data['total_folded_functions']}")
        print("  Domain Breakdown:")
        for dom, s in data["domain_summary"].items():
            print(f"    - {dom:<12}: {s['reduction_pct']:>6}% saved | avg {s['avg_ms']:>5} ms | patch_safe={s['patch_safe']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
