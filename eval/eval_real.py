"""Honest end-to-end eval of trimtok on a REAL payload corpus. No mocks.

    python eval/build_corpus.py          # (re)build eval/corpus/*.json
    python eval/eval_real.py --label before

Reduction is sum(saved)/sum(before) -- the only honest aggregate (see CHANGELOG 4.2.6).
Accuracy gates are pass/fail per payload and must not regress:
  C1 retrieve   every emitted #ref/retrieve:<hex> resolves to the exact original
  C2 faults     every important (fault) line of the original is still visible in the output
  C3 json       when both parse as JSON, they parse equal (lossless transform)
  C4 symbols    every top-level def/class name survives (code outline invariant)
  C5 verbatim   a read_file payload is byte-identical or replaced by a resolvable ref
Silent loss (changed output, no ref, not provably lossless) is always a failure.

TRIMTOK_DATA_DIR points at a throwaway dir so eval never touches ~/.trimtok telemetry.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.setdefault("TRIMTOK_DATA_DIR", str(Path(tempfile.mkdtemp(prefix="tt-eval-")))))

import trimtok as tt  # noqa: E402
from trimtok.ccr import get as ccr_get  # noqa: E402
from trimtok.core import compress_terminal  # noqa: E402
from trimtok.signals import SENTINEL_RE, Deadline, is_important  # noqa: E402

SYM_RE = re.compile(r"(?m)^\s*(?:async\s+)?(?:def|class)\s+(\w+)")
INNER_RE = re.compile(r"retrieve:([0-9a-f]{8,64})|#(c[0-9A-Za-z]{1,8}|[0-9a-f]{8,64})(?![0-9A-Za-z])")


def refs_in(out: str) -> set[str]:
    """Only refs inside real sentinel markers count -- "#ref" in the legend or "#c1"
    in prose is not a ref. SENTINEL_RE gives the marker spans."""
    refs: set[str] = set()
    for m in SENTINEL_RE.finditer(out):
        for mm in INNER_RE.finditer(m.group(0)):
            refs.add(mm.group(1) if mm.group(1) else "#" + mm.group(2))
    return refs


def json_eq(a: str, b: str) -> bool | None:
    try:
        return json.loads(a) == json.loads(b)
    except Exception:
        return None


def run_one(item: dict) -> dict:
    content = item["content"]
    deadline = Deadline(200.0)  # fresh per call: a shared one expires after the first 200ms
    t0 = time.perf_counter()
    if item["kind"] == "terminal":
        out, stats = compress_terminal(item["args"].get("cmd") or item["id"], content,
                                      session_id=item["id"], deadline=deadline)
    else:
        out, stats = tt.compress_tool_result(item["tool"], content, args=item["args"] or None,
                                            session_id=item["id"], deadline=deadline)
    ms = (time.perf_counter() - t0) * 1000.0
    out = out if isinstance(out, str) else content
    before, after = len(content), len(out)
    saved = max(0, before - after)
    # Only refs the compressor ADDED: logs and source files quote old markers.
    refs = refs_in(out) - refs_in(content)
    changed = out != content
    fails: list[str] = []

    # C1 recoverability: anything the compressor hid must come back through a ref.
    # (Refs store the unwrapped body / the omitted section, so compare missing LINES
    #  against the union of resolved payloads instead of demanding byte equality.)
    resolved = [r for r in (ccr_get(x, dir=DATA) for x in refs) if r]
    if refs and len(resolved) < len(refs):
        fails.append(f"C1-unresolved({len(refs) - len(resolved)})")
    blob = "\n".join(resolved)
    missing = [l for l in content.splitlines() if l.strip() and l.strip() not in out]
    unrecoverable = [l for l in missing if l.strip() not in blob]
    if unrecoverable:
        fails.append(f"C1-lossy({len(unrecoverable)})")

    exp = item["expect"]
    if "faults" in exp:
        hidden_faults = [l for l in content.splitlines() if is_important(l) and l.strip() and l.strip() not in out]
        if hidden_faults:
            # Hiding an error line is only "not losing intelligence" if the reader is told
            # how many were hidden AND can retrieve every one of them.
            m = re.search(r"errors_hidden=(\d+)", out[:600])
            recoverable = all(l.strip() in blob for l in hidden_faults)
            if not (m and recoverable and int(m.group(1)) >= len(hidden_faults)):
                fails.append(f"C2-faults({len(hidden_faults)})")
    if "symbols" in exp:
        lost = [s for s in set(SYM_RE.findall(content)) if s not in out]
        if lost:
            fails.append(f"C4-symbols({len(lost)})")
    if "verbatim" in exp:
        eq = json_eq(content, out)
        if not (not changed or refs or eq):
            fails.append("C5-verbatim")
    if json_eq(content, out) is False:
        fails.append("C3-json")

    return {
        "id": item["id"], "kind": item["kind"], "tool": item["tool"],
        "before": before, "after": after, "saved": saved,
        "pct": round(100.0 * saved / before, 2) if before else 0.0,
        "gate": stats.get("gate", "?"), "reason": stats.get("reason", ""),
        "shape": stats.get("shape", ""), "refs": len(refs), "missing": len(missing), "ms": round(ms, 1),
        "fails": fails,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    corpus = sorted((Path(__file__).parent / "corpus").glob("*.json"))
    if not corpus:
        print("no corpus: run eval/build_corpus.py first")
        return 2
    rows = [run_one(json.loads(p.read_text(encoding="utf-8"))) for p in corpus]

    before = sum(r["before"] for r in rows)
    after = sum(r["after"] for r in rows)
    saved = sum(r["saved"] for r in rows)
    bad = [r for r in rows if r["fails"]]
    agg = {
        "label": a.label, "payloads": len(rows), "before_chars": before, "after_chars": after,
        "saved_chars": saved, "reduction_pct": round(100.0 * saved / before, 2),
        "gate_fail_payloads": len(bad), "touched": sum(1 for r in rows if r["gate"] == "ok"),
        "ms_total": round(sum(r["ms"] for r in rows), 1),
        "ms_p95": round(sorted(r["ms"] for r in rows)[int(0.95 * (len(rows) - 1))], 1),
    }
    if not a.quiet:
        print(f"{'payload':26} {'before':>8} {'saved':>8} {'%':>6} {'gate':18} {'reason':16} {'ms':>7}  fails")
        for r in sorted(rows, key=lambda x: -x["before"]):
            print(f"{r['id'][:26]:26} {r['before']:>8} {r['saved']:>8} {r['pct']:>6.1f} "
                  f"{r['gate'][:18]:18} {str(r['reason'])[:16]:16} {r['ms']:>7.1f}  {','.join(r['fails'])}")
        print()
        print(f"REDUCTION {agg['reduction_pct']:.2f}%  ({agg['saved_chars']:,}/{agg['before_chars']:,} chars)  "
              f"payloads={agg['payloads']} touched={agg['touched']}  gates_failing={agg['gate_fail_payloads']}")
        buckets: dict[str, list[int]] = {}
        for r in rows:
            b = buckets.setdefault(r["gate"], [0, 0, 0])
            b[0] += 1
            b[1] += r["before"]
            b[2] += r["saved"]
        for k, (n, bf, sv) in sorted(buckets.items(), key=lambda kv: -kv[1][1]):
            print(f"  {k:22} n={n:3} before={bf:>9,} saved={sv:>8,}  {100.0*sv/bf if bf else 0:5.1f}%  share={100.0*bf/before:5.1f}%")
        if bad:
            print("\nFAILING GATES:")
            for r in bad:
                print(f"  {r['id']:26} {','.join(r['fails'])}")

    out = a.json or str(Path(__file__).parent / f"result-{a.label}.json")
    Path(out).write_text(json.dumps({"agg": agg, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n-> {out}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
