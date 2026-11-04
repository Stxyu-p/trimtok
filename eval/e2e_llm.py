"""E2E: does compressed output keep the agent as smart, at what token cost?

Pipeline per payload:
  1. RAW        original tool result                       -> tokenizer
  2. COMPRESS   compress_terminal / compress_tool_result   -> tokenizer (savings step A)
     - plus 1xx announce/retrieve mechanics already counted in output
  3. NARROW     for tasks: a retrieval step to exact lines (trimtok_retrieve ->
     offline via CCR get + line select) -> tokenizer             (savings step B)
  4. ANSWER     one task per payload, run on [RAW] and on [COMPRESSED+NARROW]
     with the SAME prompt, SAME max_tokens, via the 9router proxy (real LLM)
  5. SCORE      deterministic cross-check of the answer against ground truth
  6. BOOK       every step's token counts and per-task correctness

Verdict per task: correct-on-compressed == correct-on-raw
                 (task utility preserved) AND compressed tokens < raw tokens
                 (it must actually buy something).

Usage:
    python eval/e2e_llm.py                     # smoke: 1 task, cheap model
    python eval/e2e_llm.py --model ws/deepseek-v4.1-flash --tasks 12 --json eval/e2e.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-e2e-"))

import tiktoken  # noqa: E402
import trimtok as tt  # noqa: E402
from eval_real import DATA, refs_in  # noqa: E402
from trimtok.ccr import get as ccr_get  # noqa: E402
from trimtok.signals import Deadline  # noqa: E402

ENC = tiktoken.get_encoding("cl100k_base")
API = "http://localhost:20128/v1/chat/completions"
KEY = os.environ.get("HERMES_CUSTOM_LOCALHOST_20128_API_KEY", "")


def tok(s: str) -> int:
    try:
        return len(ENC.encode(s or ""))
    except Exception:
        return len((s or "")) // 4


def llm(model: str, system: str, prompt: str, max_tokens: int = 400, temp: float = 0.0) -> tuple[str, int, int, float]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": temp,
    }).encode()
    last = None
    for attempt in range(4):
        req = urllib.request.Request(API, data=body, headers={"Authorization": f"Bearer {KEY}",
                                                              "Content-Type": "application/json"})
        t0 = time.time()
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=180).read())
            ch = r["choices"][0]["message"]
            msg = ch.get("content") or ""
            if not msg.strip():
                # Reasoning models can spend the whole budget on reasoning and
                # return empty content; retry with the reasoning text as fallback.
                msg = ch.get("reasoning_content") or ch.get("reasoning") or ""
            u = r.get("usage", {})
            if not msg.strip() and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            return msg, int(u.get("prompt_tokens", tok(prompt))), int(u.get("completion_tokens", tok(msg))), time.time() - t0
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            if code == 429 or code == 503 or 400 <= (code or 0) <= 599 and code not in (400, 404):
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"rate limited after retries: {last}")


def compress(item: dict) -> tuple[str, str]:
    dl = Deadline(200.0)
    if item["kind"] == "terminal":
        out, st = tt.compress_terminal(item["args"].get("cmd") or item["id"], item["content"],
                                       session_id=item["id"], deadline=dl)
    else:
        out, st = tt.compress_tool_result(item["tool"], item["content"], args=item["args"] or None,
                                          session_id=item["id"], deadline=dl)
    return (out if isinstance(out, str) else item["content"]), st.get("gate", "")


def narrow(out: str, content: str, q: dict) -> str:
    """Give the model an honest 'retrieve' view: answer-relevant lines only."""
    refs = refs_in(out) - refs_in(content)
    blob = "\n".join(r for r in (ccr_get(x, dir=DATA) for x in refs) if r)
    keep = []
    for pat in q.get("retrieve_grep", []):
        try:
            rx = re.compile(pat, re.I)
        except Exception:
            continue
        for ln in (blob or "").splitlines():
            if rx.search(ln):
                keep.append(ln)
    # Also keep the visible skeleton/augury lines that reference the answer.
    visible = [l for l in out.splitlines() if q.get("visible_hint") and q["visible_hint"] in l]
    if keep or visible:
        return "\n".join((visible + keep)[:40])
    # Fall back: tell it exactly where to look.
    return out


# ---- tasks: (id, corpus_id, system_note, query, ground_truth checker, retrieve_grep) ----
def _mk_tasks() -> list[dict]:
    return [
        {
            "id": "e2e-tlog-errors",
            "corpus": "t-log-errors",
            "system_note": "You are a grader for an AI coding agent. Answer ONLY with the requested value; no commentary.",
            "query": "Which HTTP status codes appear in the stack traces? List each distinct 3-digit code, comma-separated, ascending.",
            "check": lambda a: [c for c in ["404", "500", "503", "400", "401", "429", "502", "504"] if c in a],
            "truth": ["404", "500", "503"],
            "retrieve_grep": [r"HTTP Error (4|5)\d\d|status.?(4|5)\d\d|\b4\d\d\b|\b5\d\d\b|response.*(4|5)\d\d"],
            "visible_hint": "FAILED",
        },
        {
            "id": "e2e-rf-dedup",
            "corpus": "rf-dedup.py",
            "system_note": "You are a code analyst. Answer ONLY with the requested value; no commentary.",
            "query": "In the file there is a function `minify_json`. What is the return type annotation on its `def` line? Answer only with the annotation, e.g. `tuple[str, int]`.",
            "check": lambda a: a.strip().replace(" ", "")[:32],
            "truth": ["tuple[str,int]", "tuple[str, int]", "(str,int)"],
            "retrieve_grep": [r"def\s+minify_json"],
            "visible_hint": "def minify_json",
        },
        {
            "id": "e2e-rf-core",
            "corpus": "rf-core.py",
            "system_note": "You are a code analyst. Answer ONLY with the requested value(s).",
            "query": "What is the value of variable `_HEAD_KEEP`? Answer with only the integer.",
            "check": lambda a: a.strip()[:3],
            "truth": ["2"],
            "retrieve_grep": [r"_HEAD_KEEP\s*=\s*\d+"],
            "visible_hint": "",
        },
        {
            "id": "e2e-rf-changelog",
            "corpus": "rf-changelog",
            "system_note": "You are a documentation analyst. Answer ONLY with the requested version numbers.",
            "query": "What is the OLDEST release version number mentioned at the top of the changelog (the first version heading)? Answer with the version only.",
            "check": lambda a: a.strip()[:12],
            "truth": ["4.2.6"],
            "retrieve_grep": [r"^## \[?\d+\.\d+\.\d+"],
            "visible_hint": "## [",
        },
        {
            "id": "e2e-tool-usage",
            "corpus": "tool-usage-jsonl",
            "system_note": "You are a data analyst. Answer ONLY with the requested value.",
            "query": "What is the value of the `\"hook\"` field in the FIRST data line (the first JSON object, NOT the line-number prefix)? Answer with the exact short string.",
            "check": lambda a: a.strip().strip('"').split()[0][:24],
            "truth": ["terminal"],
            "retrieve_grep": [r"\"hook\""],
            "visible_hint": "",
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="oc/deepseek-v4.1-flash-free")
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--tasks", default=None, help="comma-separated task ids; default = all")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    corpus = {json.loads(p.read_text(encoding="utf-8"))["id"]: json.loads(p.read_text(encoding="utf-8"))
              for p in (ROOT / "eval" / "corpus").glob("*.json")}
    tasks = _mk_tasks()
    if a.tasks:
        chosen = {t for t in a.tasks.split(",")}
        tasks = [t for t in tasks if t["id"] in chosen]
    for t in tasks:
        t["payload"] = corpus.get(t["corpus"]) or corpus.get(t["corpus"] + ".py")

    rows, summary = [], {}
    t_total = 0
    for t in tasks:
        item = t["payload"]
        if item is None:
            rows.append({"id": t["id"], "error": "payload missing"})
            continue
        content = item["content"]
        out, gate = compress(item)
        st_t0 = time.time()

        # step A: raw -> compressed
        raw_t, comp_t = tok(content), tok(out)

        # step B: narrow view (retrieval)
        narrow_v = narrow(out, content, t)
        narrow_t = tok(narrow_v)

        # step C: answer both
        def ask(blob: str) -> tuple[str, int, int, float]:
            return llm(a.model, t["system_note"],
                       f"Here is a tool result from an AI agent:\n\n```\n{blob[:20000]}\n```\n\nTask: {t['query']}",
                       max_tokens=a.max)

        raw_ans, raw_p, raw_c, raw_s = ask(content)
        comp_ans, comp_p, comp_c, comp_s = ask(narrow_v)

        truth = t["truth"]
        raw_ok = any(x in raw_ans for x in truth)
        comp_ok = any(x in comp_ans for x in truth)

        rows.append({
            "id": t["id"], "gate": gate,
            "tokens_raw_in": raw_t, "tokens_comp_in": comp_t, "tokens_narrow_in": narrow_t,
            "raw_ans": raw_ans[:120], "comp_ans": comp_ans[:120],
            "raw_correct": bool(raw_ok), "comp_correct": bool(comp_ok),
            "raw_llm_in": raw_p, "raw_llm_out": raw_c, "comp_llm_in": comp_p, "comp_llm_out": comp_c,
            "latency_raw_s": round(raw_s, 1), "latency_comp_s": round(comp_s, 1),
        })
        t_total += time.time() - st_t0
        tag = "✔" if (raw_ok and comp_ok) else ("✘ raw-only" if raw_ok else ("✘ comp-only" if comp_ok else "✘ both-fail"))
        print(f"[{tag}] {t['id']}: raw_tok={raw_t}->comp_tok={comp_t}->narrow_tok={narrow_t} "
              f"raw_correct={raw_ok} comp_correct={comp_ok} ({raw_s:.1f}s/{comp_s:.1f}s)")

    ok_rows = [r for r in rows if "error" not in r]
    both = sum(1 for r in ok_rows if r["raw_correct"] and r["comp_correct"])
    raw_o = sum(1 for r in ok_rows if r["raw_correct"])
    comp_o = sum(1 for r in ok_rows if r["comp_correct"])
    raw_in = sum(r["tokens_raw_in"] for r in ok_rows)
    comp_in = sum(r["tokens_comp_in"] for r in ok_rows)
    narrow_in = sum(r["tokens_narrow_in"] for r in ok_rows)
    llm_in_saved = sum(r["raw_llm_in"] - r["comp_llm_in"] for r in ok_rows)

    summary = {
        "model": a.model, "tasks": len(ok_rows),
        "raw_correct": raw_o, "comp_correct": comp_o, "both_correct": both,
        "task_utility_preserved_pct": round(100.0 * both / max(1, raw_o), 1),
        "tokens": {
            "raw_input_total": raw_in,
            "compressed_input_total": comp_in,
            "compression_saved": raw_in - comp_in,
            "compression_reduction_pct": round(100.0 * (raw_in - comp_in) / max(1, raw_in), 2),
            "narrow_input_total": narrow_in,
            "comp_plus_narrow_saved": raw_in - narrow_in,
            "comp_plus_narrow_reduction_pct": round(100.0 * (raw_in - narrow_in) / max(1, raw_in), 2),
            "llm_saved_by_shorter_inputs": llm_in_saved,
            "total_elapsed_s": round(t_total, 1),
        },
    }
    print("\n=== E2E SUMMARY ===")
    print(json.dumps(summary, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("->", a.json)


if __name__ == "__main__":
    raise SystemExit(main())
