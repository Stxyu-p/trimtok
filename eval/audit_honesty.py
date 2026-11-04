"""How much of trimtok's reduction would survive if we told the truth?

The visible text is the product. The header says "hidden=1838". A reader who
trusts that number will not re-read. So any line hidden WITHOUT being counted,
or any line that cannot be recovered through a #ref, is a lie -- not a saving.

This script audits the three claims trimtok makes, per payload:

  CLAIM 1  every hidden line resolves through a #ref byte-exactly
  CLAIM 2  the output TELLS the reader content is missing (header count or an
           in-body `[... N lines collapsed ...]` marker) -- numbered read_file
           payloads announce inline, not in the header
  CLAIM 3  emitted `LINE|` numbers stay strictly ascending, so a patch lands on
           the line it names

Then it separates the corpus into:
  PROVEN SAVINGS     compressed, announced, refs resolve, anchors ordered
  DEAD SAVINGS       bytes removed but at least one claim does not hold

Usage: python eval/audit_honesty.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-honest-"))

import trimtok as tt  # noqa: E402
from eval_real import DATA, refs_in  # noqa: E402
from trimtok.ccr import get as ccr_get  # noqa: E402
from trimtok.signals import Deadline, SENTINEL_RE  # noqa: E402

HEAD_COUNT_RES = [
    re.compile(r"hidden=(\d+)"),
    re.compile(r"errors_hidden=(\d+)"),
    re.compile(r"dropped=(\d+)"),
    re.compile(r"\(\s*(\d+)\s*lines? omitted"),
    re.compile(r"\.\.\.\s*(\d+)\s*(?:unchanged )?lines? (?:omitted|collapsed)"),
]

# Any of these means the output told the reader content is missing.
# Terminal headers use hidden=/errors_hidden=; file_skeleton/file_structure
# announce per-fold with in-body markers `[tt:body L89-L98 (10L of fn) #c1 ->
# read_file(offset=89, limit=10)...]` and `[tt:prose L11-L25 (15L) #c1 -> ...]`.
ANNOUNCE_RE = re.compile(
    r"hidden=\d+|errors_hidden=[1-9]\d*|dropped=[1-9]\d*|"
    r"\[\.\.\.\s*\d+\s+(?:unchanged )?lines?|collapsed|omitted|"
    r"\[tt:(?:body|prose) L\d+-L\d+ \(\d+L"
)
NUM_RE = re.compile(r"(?m)^\s*(\d+)\|")


def declared_hidden(out: str) -> int:
    """Sum every hidden/omitted count the header and fold markers announce."""
    seen = 0
    for rx in HEAD_COUNT_RES:
        target = out[:800] if rx.pattern.startswith(("hidden", "errors_hidden", "dropped")) else out
        seen = max(seen, sum(int(m) for m in rx.findall(target)))
    return seen


def probe(item: dict) -> dict:
    content = item["content"]
    dl = Deadline(200.0)
    if item["kind"] == "terminal":
        out, st = tt.compress_terminal(item["args"].get("cmd") or item["id"], content,
                                       session_id=item["id"], deadline=dl)
    else:
        out, st = tt.compress_tool_result(item["tool"], content, args=item["args"] or None,
                                          session_id=item["id"], deadline=dl)
    out = out if isinstance(out, str) else content

    refs = refs_in(out) - refs_in(content)
    blob = "\n".join(r for r in (ccr_get(x, dir=DATA) for x in refs) if r)

    orig = [l for l in content.splitlines() if l.strip()]
    hidden = [l for l in orig if l.strip() not in out]
    unresolved = [l for l in hidden if l.strip() not in blob]

    # Claim 2: did the output announce that content is missing?
    announced = bool(ANNOUNCE_RE.search(out)) if hidden else True

    # Claim 3: patch anchors for numbered payloads.
    nums = [int(m) for m in NUM_RE.findall(out)]
    patch_safe = all(b > a for a, b in zip(nums, nums[1:])) if len(nums) > 1 else True

    before, after = len(content), len(out)
    saved = max(0, before - after)
    proven = saved if (not unresolved and announced and patch_safe) else 0

    return {
        "id": item["id"], "kind": item["kind"], "before": before, "after": after,
        "saved": saved, "gate": st.get("gate"), "reason": st.get("reason", ""),
        "hidden": len(hidden), "declared": declared_hidden(out),
        "unresolved": len(unresolved), "announced": announced,
        "numbered": bool(nums), "patch_safe": patch_safe, "proven": max(0, proven),
    }


def main() -> int:
    corpus = sorted((Path(__file__).parent / "corpus").glob("*.json"))
    rows = [probe(json.loads(p.read_text(encoding="utf-8"))) for p in corpus]

    print(f"{'payload':30} {'saved':>7} {'hid':>5} {'unres':>6} {'ann':>4} {'patch':>5}  verdict")
    print("-" * 92)
    for r in sorted(rows, key=lambda x: -x["saved"]):
        ok = not r["unresolved"] and r["announced"] and r["patch_safe"]
        verdict = "ok" if ok else ("DEAD" if r["saved"] else "zero")
        print(f"{r['id'][:30]:30} {r['saved']:7d} {r['hidden']:5d} {r['unresolved']:6d} "
              f"{'y' if r['announced'] else 'N':>4} {'y' if r['patch_safe'] else 'N':>5}  {verdict}")

    before = sum(r["before"] for r in rows)
    saved = sum(r["saved"] for r in rows)
    proven = sum(r["proven"] for r in rows)
    dead = saved - proven
    dead_rows = [r for r in rows if r["saved"] and r["proven"] == 0]

    print()
    print(f"CLAIMED REDUCTION  {100.0*saved/before:6.2f}%   ({saved:,} chars)")
    print(f"PROVEN  REDUCTION  {100.0*proven/before:6.2f}%   ({proven:,} chars)")
    print(f"UNPROVEN           {dead:,} chars  ({100.0*dead/max(1,saved):.1f}% of claimed)")
    for r in dead_rows:
        why = []
        if r["unresolved"]:
            why.append(f"unresolved={r['unresolved']}")
        if not r["announced"]:
            why.append("silent-hide")
        if not r["patch_safe"]:
            why.append("patch-order")
        print(f"   {r['id'][:30]:30} {r['saved']:7d}  {', '.join(why)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
