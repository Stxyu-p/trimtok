"""Per-step token accounting over the real corpus (fast, no LLM).

For every payload report the four stages an agent actually pays for:

  RAW      the tool result as produced
  HOOK     after compress_terminal / compress_tool_result  (always on)
  NARROW   after trimtok_retrieve --intent <task>          (on demand, per question)

and the two derived reductions. This is the cheap stand-in for e2e_llm.py: it
measures the bytes the model is billed for, not whether the model answers
correctly (that needs a live LLM and lives in e2e_llm.py).

Usage: python eval/steps.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-steps-"))

import tiktoken  # noqa: E402
import trimtok as tt  # noqa: E402
from trimtok.ccr import put as ccr_put  # noqa: E402
from trimtok.signals import Deadline  # noqa: E402

ENC = tiktoken.get_encoding("cl100k_base")

# One realistic question per payload shape: what an agent would ask next.
INTENTS = {
    "rf-dedup.py": "which functions does this file define, signatures",
    "rf-__init__.py": "which functions and tools does this file expose",
    "rf-ccr.py": "how is content stored and retrieved",
    "rf-pruners.py": "which pruners are defined and what do they do",
    "rf-core.py": "compression pipeline stages and constants",
    "rf-signals.py": "how are sentinels and fault lines detected",
    "rf-toon_codec.py": "how does the json to toon encoding work",
    "rf-changelog": "release versions in the changelog",
    "rf-readme": "what features and benchmarks are listed",
    "rf-skill-systematic-debugging": "debugging procedure steps",
    "rf-skill-grounded-citations": "citation rules",
    "rf-skill-context-compression-maintenance": "audit procedure rules",
    "t-log-agent": "which api calls failed or retried",
    "t-log-errors": "which http status codes and errors occurred",
    "t-log-gateway-stdio": "which errors occurred in the gateway",
    "t-grep-def": "where are functions defined",
    "t-npm-ls": "which packages are installed",
    "tool-usage-jsonl": "which gates saved the most tokens",
    "tool-skillview": "what does this skill instruct",
    "tool-plugin-yaml": "what config options exist",
}
DEFAULT_INTENT = "the important errors, values and identifiers"


def tok(s: str) -> int:
    try:
        return len(ENC.encode(s or ""))
    except Exception:
        return len(s or "") // 4


def main() -> int:
    rows = []
    for p in sorted((Path(__file__).parent / "corpus").glob("*.json")):
        item = json.loads(p.read_text(encoding="utf-8"))
        content = item["content"]
        dl = Deadline(200.0)
        if item["kind"] == "terminal":
            hook, st = tt.compress_terminal(item["args"].get("cmd") or item["id"], content,
                                            session_id=item["id"], deadline=dl)
        else:
            hook, st = tt.compress_tool_result(item["tool"], content, args=item["args"] or None,
                                               session_id=item["id"], deadline=dl)
        hook = hook if isinstance(hook, str) else content

        # NARROW: what the agent reads when it asks a specific question.
        intent = INTENTS.get(item["id"], DEFAULT_INTENT)
        narrow = hook
        ref = ccr_put(content)
        if ref:
            from trimtok import _handle_retrieve
            try:
                payload = json.loads(_handle_retrieve({"hash": ref, "intent": intent, "intent_budget": 80}))
                cand = payload.get("lines") or ""
                if cand and tok(cand) < tok(hook):
                    narrow = cand
            except Exception:
                pass

        t_raw, t_hook, t_narrow = tok(content), tok(hook), tok(narrow)
        rows.append({
            "id": item["id"], "kind": item["kind"], "gate": st.get("gate"),
            "raw": t_raw, "hook": t_hook, "narrow": t_narrow,
        })

    print(f"{'payload':36} {'RAW':>7} {'HOOK':>7} {'NARROW':>7}  {'hook%':>6} {'narrow%':>8}")
    print("-" * 86)
    for r in sorted(rows, key=lambda x: -x["raw"]):
        h = 100.0 * (1 - r["hook"] / r["raw"]) if r["raw"] else 0.0
        n = 100.0 * (1 - r["narrow"] / r["raw"]) if r["raw"] else 0.0
        print(f"{r['id'][:36]:36} {r['raw']:7d} {r['hook']:7d} {r['narrow']:7d}  {h:6.1f} {n:8.1f}")

    raw = sum(r["raw"] for r in rows)
    hook = sum(r["hook"] for r in rows)
    narrow = sum(r["narrow"] for r in rows)
    print()
    print(f"TOTAL RAW     {raw:,} tok")
    print(f"AFTER HOOK    {hook:,} tok   ({100.0*(raw-hook)/raw:.2f}% saved, always on)")
    print(f"AFTER NARROW  {narrow:,} tok   ({100.0*(raw-narrow)/raw:.2f}% saved, per question)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
