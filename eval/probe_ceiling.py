"""Ceiling probe: how far can trimtok go before intelligence is harmed?

For each corpus payload, run the real compressor, then measure what an agent
would actually lose by reading the compressed text instead of the original.
Nothing here compresses harder -- it only *scores* the current output so the
"max safe reduction" number is grounded in evidence, not vibes.

Probes per payload:
  NEEDLE  every line of the original is hidden -> is it in the CCR blob?
          (binary: recoverable or silently lost)
  ANCHOR  navigation anchors (file:line, paths, test ids, error classes) that
          survive in the visible text -- what the agent can act on without a
          second tool call.
  FAULT   fault lines hidden but announced (errors_hidden=N) vs unannounced.
  VERBATIM-ONLY  payloads that carried 0 reduction: why (gate+reason).

Usage:  python eval/probe_ceiling.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import os
import tempfile

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-probe-"))

from eval_real import run_one, DATA  # noqa: E402
from trimtok.ccr import get as ccr_get  # noqa: E402
from trimtok.signals import SENTINEL_RE, is_important  # noqa: E402

LIMIT = re.compile(r"\d{1,6}")

# Navigation anchors: things an agent must see to act without another read.
ANCHOR_RES = {
    "file:line": re.compile(r"[\w./\\-]+\.\w{1,5}:\d{1,6}"),
    "file L<n>": re.compile(r"[\w./\\-]+\.\w{1,5}[ ,]*(?:L|line |:)\d{1,6}"),
    "path": re.compile(r"(?:[\w.-]+/){2,}[\w.-]+"),
    "test id": re.compile(r"[\w./-]+::\w+"),
    "exc class": re.compile(r"\b[A-Z]\w{2,}(?:Error|Exception|Failure|Warning)\b"),
    "http code": re.compile(r"\b[45]\d{2}\b"),
    "assert": re.compile(r"\bassert\w*\b", re.I),
    "exit code": re.compile(r"exit(?:ed)? (?:code|status) \d+", re.I),
}


def anchors(text: str) -> set[str]:
    out: set[str] = set()
    for name, rx in ANCHOR_RES.items():
        for m in rx.findall(text):
            out.add(m if isinstance(m, str) else str(m))
    return out


def probe(item: dict) -> dict:
    content = item["content"]
    row = run_one(item)
    out_text = None
    # Re-run to keep the emitted text (run_one returns stats only).
    import trimtok as tt
    from trimtok.core import compress_terminal
    from trimtok.signals import Deadline

    dl = Deadline(200.0)
    if item["kind"] == "terminal":
        out_text, _ = compress_terminal(item["args"].get("cmd") or item["id"], content,
                                        session_id=item["id"], deadline=dl)
    else:
        out_text, _ = tt.compress_tool_result(item["tool"], content, args=item["args"] or None,
                                              session_id=item["id"], deadline=dl)
    out_text = out_text if isinstance(out_text, str) else content

    # Reuse eval_real's ref extraction to stay consistent.
    from eval_real import refs_in
    refs = refs_in(out_text) - refs_in(content)
    blob = "\n".join(r for r in (ccr_get(x, dir=DATA) for x in refs) if r)

    orig_lines = [l for l in content.splitlines() if l.strip()]
    hidden = [l for l in orig_lines if l.strip() not in out_text]
    lost = [l for l in hidden if l.strip() not in blob]

    # Fault contract applies to terminal/log payloads only: is_important on
    # read_file data (code, yaml, jsonl) matches field values and comment text
    # that is not an agent-relevant error signal (see known-pitfalls).
    fault_lines = fault_hidden = []
    announced = 0
    fault_unannounced = 0
    if item["kind"] == "terminal":
        fault_lines = [l for l in orig_lines if is_important(l)]
        fault_hidden = [l for l in fault_lines if l.strip() not in out_text]
        m = re.search(r"errors_hidden=(\d+)", out_text[:600])
        announced = int(m.group(1)) if m else 0
        fault_unannounced = max(0, len(fault_hidden) - announced)

    a_orig = anchors(content)
    a_keep = anchors(out_text)
    anchor_ratio = (len(a_keep) / len(a_orig)) if a_orig else 1.0

    return {
        "id": item["id"],
        "kind": item["kind"],
        "tool": item["tool"],
        "before": row["before"],
        "after": row["after"],
        "pct": row["pct"],
        "gate": row["gate"],
        "reason": row["reason"],
        "refs": len(refs),
        "lines_total": len(orig_lines),
        "lines_hidden": len(hidden),
        "lines_lost": len(lost),
        "faults_total": len(fault_lines),
        "faults_hidden": len(fault_hidden),
        "faults_unannounced": fault_unannounced,
        "anchors_orig": len(a_orig),
        "anchors_keep": len(a_keep),
        "anchor_ratio": round(anchor_ratio, 3),
        "fails": row["fails"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    corpus = sorted((Path(__file__).parent / "corpus").glob("*.json"))
    rows = [probe(json.loads(p.read_text(encoding="utf-8"))) for p in corpus]

    before = sum(r["before"] for r in rows)
    after = sum(r["after"] for r in rows)
    saved = before - after

    print(f"{'payload':28} {'before':>7} {'saved':>7} {'%':>6} {'anch':>6} "
          f"{'hide':>6} {'lost':>5} {'fUn':>4}  gate/reason")
    print("-" * 108)
    for r in sorted(rows, key=lambda x: -x["before"]):
        print(f"{r['id'][:28]:28} {r['before']:7d} {r['after'] and r['before']-r['after']:7d} "
              f"{r['pct']:6.1f} {r['anchor_ratio']:6.2f} {r['lines_hidden']:6d} "
              f"{r['lines_lost']:5d} {r['faults_unannounced']:4d}  {r['gate']}/{r['reason']}")

    total_anch = sum(r["anchors_keep"] for r in rows) / max(1, sum(r["anchors_orig"] for r in rows))
    zero = [r for r in rows if r["pct"] == 0.0]
    zero_chars = sum(r["before"] for r in zero)
    lost_total = sum(r["lines_lost"] for r in rows)
    fault_un = sum(r["faults_unannounced"] for r in rows)

    print()
    print(f"REDUCTION            {100.0*saved/before:.2f}%  ({saved:,}/{before:,} chars)")
    print(f"ANCHOR RETENTION     {100*total_anch:.1f}%  (navigation anchors visible, no second read)")
    print(f"SILENT LINE LOSS     {lost_total}  (must be 0 -- anything here IS lost intelligence)")
    print(f"UNANNOUNCED FAULTS   {fault_un}  (must be 0)")
    print(f"ZERO-REDUCTION       {len(zero)} payloads / {zero_chars:,} chars = {100.0*zero_chars/before:.1f}% of corpus")
    for r in sorted(zero, key=lambda x: -x["before"]):
        print(f"   {r['id'][:30]:30} {r['before']:7d}  {r['gate']}/{r['reason']}")

    if a.json:
        Path(a.json).write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
