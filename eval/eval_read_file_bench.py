"""Multi-approach read_file benchmark runner and metric evaluator.

Compares strategies across:
1. Token Reduction % (saved_chars / before_chars)
2. In-process Latency (ms)
3. Patch Safety (line anchor and number prefix fidelity)
4. Comprehension Metrics (kept short utility functions vs stripped logic)
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
    """Ensure transformed output preserves original line numbering for patch safety."""
    orig_lines = original.split("\n")
    # Check if original is numbered (Hermes read_file convention)
    orig_map: dict[int, str] = {}
    is_numbered = False
    for line in orig_lines:
        m = _LINE_NO_RE.match(line)
        if m:
            is_numbered = True
            orig_map[int(m.group(1))] = m.group(2)

    if not is_numbered:
        # Non-numbered payload (e.g. raw web search) has no line prefixes to check
        return True

    # For numbered payloads, check that lines in transformed retain valid prefixes
    trans_lines = transformed.split("\n")
    checked = 0
    for line in trans_lines:
        s = line.strip()
        if not s or s.startswith(("[tt:", "---", "/*", "//", "Outline", "@")):
            continue
        m = _LINE_NO_RE.match(line)
        if not m:
            # Code line exists without line prefix -> fails patch safety
            return False
        line_no = int(m.group(1))
        content = m.group(2)
        if line_no in orig_map:
            # Check content matches (or whitespace trimmed)
            if orig_map[line_no].strip() != content.strip() and not content.strip().startswith("..."):
                return False
            checked += 1

    return checked > 0 or len(trans_lines) < 5


def evaluate_strategy_on_payload(
    strategy_fn: Callable[[str, dict], tuple[str, dict]],
    payload: dict[str, Any],
) -> BenchmarkMetric:
    """Run one strategy on a payload and compute metrics."""
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


def main():
    parser = argparse.ArgumentParser(description="Multi-approach read_file benchmark")
    parser.add_argument("--json", action="store_true", help="output json results")
    args = parser.parse_args()

    corpus = load_corpus()
    print(f"Loaded {len(corpus)} evaluation payloads.")

if __name__ == "__main__":
    main()
