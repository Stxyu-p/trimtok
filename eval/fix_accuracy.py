"""Fix-accuracy benchmark: does the agent still *fix* the bug, not just save tokens?

Token counts say nothing about whether the model can still do the job. This
benchmark injects a known regression into a real module, runs it through the
compression pipeline, and asks one question: **can the bug still be fixed from
what the agent is allowed to read?**

Two stages, deliberately separated:

  LOCATE (default, no LLM, deterministic)
      Is the buggy line, its enclosing signature, its line number and at least
      one line of context still visible -- or at least reachable through
      `trimtok_retrieve`? A payload that hides the bug without a ref is a
      FAIL, because the agent would have to re-read the file.

  FIX (--llm, needs a live model)
      Give the model the compressed view only, take back the corrected line,
      patch the real module, import it, and run a behavioural predicate. PASS
      means the fixed code actually works, not that the text looks plausible.

Usage:
  python eval/fix_accuracy.py                # locate stage, instant
  python eval/fix_accuracy.py --llm          # + real model, real patch
  python eval/fix_accuracy.py --llm --model oc/deepseek-v4.1-flash-free
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
os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-fixacc-"))

try:
    import tiktoken  # noqa: E402
    ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    ENC = None
from trimtok import _handle_retrieve  # noqa: E402
from trimtok.ccr import get as ccr_get  # noqa: E402
from trimtok.ccr import put as ccr_put  # noqa: E402
from trimtok.dedup import compress_tool_result  # noqa: E402
from trimtok.signals import Deadline, estimate_tokens  # noqa: E402

API = "http://localhost:20128/v1/chat/completions"
KEY = os.environ.get("HERMES_CUSTOM_LOCALHOST_20128_API_KEY", "")

# Each task: a real source file, a one-line regression, and a predicate that the
# repaired module must satisfy. `original` must appear exactly once in the file.
TASKS = [
    {
        "id": "policy-level-threshold",
        "file": "trimtok/policy.py",
        "original": "    if p < 0.75:",
        "buggy": "    if p < 0.075:",
        "probe": "from trimtok.policy import level_for_pressure as f\n"
                 "assert f(0.5) == 'normal', f(0.5)\n"
                 "assert f(0.9) == 'aggressive', f(0.9)",
        "ask": "level_for_pressure(0.5) must return 'normal' and level_for_pressure(0.9) must return 'aggressive'",
    },
    {
        "id": "policy-budget-clamp",
        "file": "trimtok/policy.py",
        "original": "    return max(10, int(default * mult))",
        "buggy": "    return min(10, int(default * mult))",
        "probe": "from trimtok.policy import budget_for as f\n"
                 "assert f('aggressive', 120) == 66, f('aggressive', 120)\n"
                 "assert f('light', 120) == 192, f('light', 120)",
        "ask": "budget_for('aggressive', 120) must return 66 and budget_for('light', 120) must return 192",
    },
    {
        "id": "policy-fold-threshold",
        "file": "trimtok/policy.py",
        "original": '    return _LEVEL_FOLD.get(level, 8)',
        "buggy": '    return _LEVEL_FOLD.get(level, 80)',
        "probe": "from trimtok.policy import fold_min_lines as f\n"
                 "assert f('normal') == 8, f('normal')\n"
                 "assert f('aggressive') == 4, f('aggressive')",
        "ask": "fold_min_lines('normal') must return 8 and fold_min_lines('aggressive') must return 4",
    },
    {
        "id": "policy-secret-path",
        "file": "trimtok/policy.py",
        "original": 'r"id_rsa|id_ed25519|id_ecdsa|.*\\.pem|.*\\.key|.*\\.p12|.*\\.pfx|"',
        "buggy": 'r"id_rsa|id_ed25519|id_ecdsa|.*\\.pem|.*\\.p12|.*\\.pfx|"',
        "probe": "from trimtok.policy import classify\n"
                 "assert classify('x'*100, path='server.key').verbatim is True\n"
                 "assert classify('x'*100, path='server.pem').verbatim is True",
        "ask": "a file named server.key must be classified verbatim (secret_path)",
    },
    {
        "id": "core-progress-regex",
        "file": "trimtok/core.py",
        "original": '_PROGRESS_RE = re.compile(',
        "buggy": '_PROGRESS_RE = re.compile(',
        "probe": "from trimtok.core import _PROGRESS_RE as r\n"
                 "assert r.match('- fix the bug') is None, 'a diff line is not a spinner'\n"
                 "assert r.match('- 40%') is not None",
        "ask": "a line starting with '- ' followed by text must NOT match the progress regex, but '- 40%' must",
        "mutate": lambda s: s.replace(
            '_PROGRESS_RE = re.compile(',
            '_PROGRESS_RE = re.compile(\n    r"^\\\\s*[-/\\\\\\\\|]"  # BUG: bare dash matches diffs\n)\n_PROGRESS_RE_ORIG = re.compile(', 1),
    },
    {
        "id": "toon-cell-pipe",
        "file": "trimtok/toon_codec.py",
        "original": "def _split_cells(",
        "buggy": "def _split_cells(",
        "probe": "from trimtok.toon_codec import _split_cells as f\n"
                 "got = f('a|\"b|c\"|d')\n"
                 "assert got == ['a', 'b|c', 'd'], got",
        "ask": "splitting a TOON row must keep a quoted pipe inside one cell: 'a|\"b|c\"|d' -> ['a','b|c','d']",
        "mutate": lambda s: s.replace(
            "def _split_cells(",
            "def _split_cells(s: str):\n"
            "    return s.split('|')  # BUG: ignores quoting\n\n\n"
            "def _split_cells_orig(", 1),
    },
    {
        "id": "dedup-minify-whitespace",
        "file": "trimtok/dedup.py",
        "original": "def minify_json(",
        "buggy": "def minify_json(",
        "probe": "import json\nfrom trimtok.dedup import minify_json as f\n"
                 "src = json.dumps({'a': [1, 2], 'b': {'c': 'x y'}}, indent=2)\n"
                 "out, saved = f(src)\n"
                 "assert json.loads(out) == json.loads(src), out\n"
                 "assert '\\n' not in out, repr(out[:80])",
        "ask": "minify_json must return the same JSON on one line with no newlines",
        "mutate": lambda s: s.replace(
            "def minify_json(",
            "def minify_json(text: str):\n"
            "    return text, 0  # BUG: never minifies\n\n\n"
            "def minify_json_orig(", 1),
    },
    {
        "id": "signals-sentinel-gate",
        "file": "trimtok/signals.py",
        "original": "SENTINEL_RE = re.compile(",
        "buggy": "SENTINEL_RE = re.compile(",
        "probe": "from trimtok.signals import SENTINEL_RE as r\n"
                 "assert r.search('[tt:file_unchanged #abc path=\"x\"]'), 'file_unchanged sentinel'\n"
                 "assert r.search('[tt:lockfile path=\"y\" #def]'), 'lockfile sentinel'\n"
                 "assert r.search('[tt:tool kind=result saved=10 #abc]'), 'tool sentinel'",
        "ask": "SENTINEL_RE must match [tt:file_unchanged ...], [tt:lockfile ...] and [tt:tool ...]",
        "mutate": lambda s: s.replace(
            "SENTINEL_RE = re.compile(",
            "SENTINEL_RE = re.compile(r'\\\\[tt:tool kind=')  # BUG: only the tool sentinel\n_SENTINEL_RE_ORIG = re.compile(", 1),
    },
]


def tok(s: str) -> int:
    try:
        if ENC is not None:
            return len(ENC.encode(s or ""))
    except Exception:
        pass
    return estimate_tokens(s or "")


def apply_mutation(task: dict, src: str) -> str:
    mut = task.get("mutate")
    if mut:
        out = mut(src)
        if out == src:
            raise RuntimeError(f"{task['id']}: mutation did not apply")
        return out
    if task["original"] not in src:
        raise RuntimeError(f"{task['id']}: anchor not found")
    return src.replace(task["original"], task["buggy"], 1)


def line_of(src: str, needle: str) -> int:
    for i, ln in enumerate(src.splitlines(), 1):
        if needle.strip() and needle.strip() in ln:
            return i
    return 0


def payload_of(path: Path, text: str) -> str:
    """Hermes-shaped read_file payload: `N|content`."""
    return "\n".join(f"{i}|{ln}" for i, ln in enumerate(text.splitlines(), 1))


def strip_prefixes(text: str) -> str:
    return re.sub(r"(?m)^\s*\d+\|", "", text)


def locate(task: dict, view: str, ref: str, buggy_line: str, sig_line: str, lineno: int) -> dict:
    """Where does the agent find each thing it needs to write the fix?"""
    plain = strip_prefixes(view)
    blob_raw = ccr_get(ref) or "" if ref else ""
    blob = strip_prefixes(blob_raw)

    def where(needle: str) -> str:
        if not needle.strip():
            return "n/a"
        if needle.strip() in plain:
            return "view"
        if needle.strip() in blob:
            return "ref"
        return "lost"

    # Neighbour context: the two lines around the bug, so the model can see the
    # enclosing block instead of one orphan line.
    neigh = []
    lines = task["_src"].splitlines()
    for d in (-1, 1):
        idx = lineno - 1 + d
        if 0 <= idx < len(lines):
            neigh.append(where(lines[idx]))

    return {
        "buggy_line": where(buggy_line),
        "signature": where(sig_line),
        "line_number": "view" if re.search(rf"(?m)^\s*{lineno}\|", view) else (
            "ref" if re.search(rf"(?m)^\s*{lineno}\|", blob_raw) else "lost"),
        "context": "lost" if neigh and all(x == "lost" for x in neigh) else (
            "view" if "view" in neigh else "ref"),
    }


def llm_fix(model: str, view: str, ask: str, lineno: int, max_tokens: int = 700) -> str:
    system = ("You are a senior Python engineer. You get ONE compressed file view. "
              "Reply with the corrected line only, formatted as `N|code` where N is the "
              "line number shown in the view. No prose, no markdown fence.")
    prompt = f"Bug: {ask}\n\nCompressed view:\n{view[:24000]}\n\nCorrected line:"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0.0,
    }).encode()
    last: Exception | None = None
    for attempt in range(3):
        req = urllib.request.Request(API, data=body, headers={
            "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=180).read())
            ch = r["choices"][0]["message"]
            msg = ch.get("content") or ch.get("reasoning_content") or ""
            if msg.strip():
                return msg.strip()
            last = RuntimeError("empty completion")
        except Exception as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"llm unavailable: {last}")


def extract_line(reply: str, lineno: int, indent: str = "") -> str | None:
    """Pull `N|code` out of a model reply and re-apply the original indentation.

    Models routinely drop or double leading whitespace; the correct indent for a
    one-line fix is the indentation the buggy line already had, so trust that
    instead of the reply's spacing.
    """
    def _norm(code: str) -> str:
        return (indent + code.lstrip()) if indent else code

    m = re.search(rf"(?m)^[ \t]*{lineno}\|(.*)$", reply)
    if m:
        return _norm(m.group(1))
    for ln in reply.splitlines():
        m = re.search(r"^[ \t]*(\d+)\|(.*)$", ln)
        if m:
            return _norm(m.group(2))
    for ln in reply.splitlines():
        s = ln.strip().strip("`")
        if s and not s.lower().startswith(("here", "the corrected", "```")):
            return _norm(s)
    return None


def run_probe(code: str) -> tuple[bool, str]:
    """Execute the predicate against a patched copy of the module tree."""
    tmp = Path(tempfile.mkdtemp(prefix="tt-probe-"))
    try:
        import shutil
        for name in ("trimtok",):
            shutil.copytree(ROOT / name, tmp / name, ignore=shutil.ignore_patterns("__pycache__"))
        return _exec_probe(tmp, code)
    finally:
        pass


def _exec_probe(root: Path, code: str) -> tuple[bool, str]:
    import subprocess
    p = subprocess.run([sys.executable, "-c", code], cwd=str(root),
                       capture_output=True, text=True, timeout=120)
    if p.returncode == 0:
        return True, ""
    err = (p.stderr or "").strip().splitlines()
    return False, err[-1] if err else f"exit {p.returncode}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also run the real fix stage (needs a live model)")
    ap.add_argument("--model", default="oc/deepseek-v4.1-flash-free")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    rows = []
    for task in TASKS:
        path = ROOT / task["file"]
        src = path.read_text(encoding="utf-8")
        try:
            buggy_src = apply_mutation(task, src)
        except RuntimeError as exc:
            rows.append({"id": task["id"], "status": "SETUP_FAIL", "detail": str(exc)})
            print(f"[SETUP_FAIL] {task['id']}: {exc}")
            continue

        task["_src"] = buggy_src
        lineno = line_of(buggy_src, task["buggy"]) or line_of(buggy_src, task["original"])
        buggy_line = buggy_src.splitlines()[lineno - 1]
        sig_line = ""
        for i in range(lineno - 1, -1, -1):
            if re.match(r"\s*(?:async\s+)?def\s+\w+", buggy_src.splitlines()[i]):
                sig_line = buggy_src.splitlines()[i]
                break

        payload = payload_of(path, buggy_src)
        view, st = compress_tool_result("read_file", payload, args={"path": task["file"]},
                                        session_id=f"fixacc-{task['id']}",
                                        deadline=Deadline(500.0))
        ref = st.get("digest") or ""
        if not ref:
            m = re.search(r"#([0-9a-f]{8,})", view)
            ref = m.group(1) if m else ""

        found = locate(task, view, ref, buggy_line, sig_line, lineno)
        lost = [k for k, v in found.items() if v == "lost"]
        status = "PASS" if not lost else "FAIL"

        row = {
            "id": task["id"], "file": task["file"], "line": lineno,
            "status": status, "found": found, "lost": lost,
            "tok_before": tok(payload), "tok_after": tok(view),
            "gate": st.get("gate"), "kind": st.get("kind"),
        }

        if args.llm:
            try:
                reply = llm_fix(args.model, view, task["ask"], lineno)
                fixed = extract_line(reply, lineno, indent=buggy_line[:len(buggy_line) - len(buggy_line.lstrip())])
                row["llm_reply"] = (reply or "")[:300]
                if not fixed:
                    row.update(status="FAIL", fix="no_line")
                else:
                    lines = buggy_src.splitlines()
                    lines[lineno - 1] = fixed
                    patched = "\n".join(lines) + "\n"
                    tmp = Path(tempfile.mkdtemp(prefix="tt-fix-"))
                    import shutil
                    shutil.copytree(ROOT / "trimtok", tmp / "trimtok",
                                    ignore=shutil.ignore_patterns("__pycache__"))
                    (tmp / task["file"]).write_text(patched, encoding="utf-8")
                    ok, err = _exec_probe(tmp, task["probe"])
                    row["fix"] = "correct" if ok else "wrong"
                    row["fix_error"] = err
                    if not ok:
                        row["status"] = "FAIL"
            except Exception as exc:
                row["fix"] = f"unavailable: {type(exc).__name__}"

        rows.append(row)
        pct = 100.0 * (1 - row["tok_after"] / row["tok_before"]) if row["tok_before"] else 0.0
        detail = "" if status == "PASS" else f"  lost={','.join(lost)}"
        fixnote = f"  fix={row.get('fix')}" if row.get("fix") else ""
        print(f"[{status}] {task['id']:26} {row['tok_before']:6d} -> {row['tok_after']:6d} tok "
              f"({pct:5.1f}%)  {found['buggy_line']}/{found['signature']}/{found['context']}{detail}{fixnote}")

    passed = sum(1 for r in rows if r["status"] == "PASS")
    print()
    print(f"LOCATE ACCURACY  {passed}/{len(rows)}  ({100.0*passed/max(1,len(rows)):.1f}%)")
    if args.llm:
        fixed = sum(1 for r in rows if r.get("fix") == "correct")
        print(f"FIX ACCURACY     {fixed}/{len(rows)}  ({100.0*fixed/max(1,len(rows)):.1f}%)")
    tb = sum(r.get("tok_before", 0) for r in rows)
    ta = sum(r.get("tok_after", 0) for r in rows)
    if tb:
        print(f"TOKENS           {tb:,} -> {ta:,}  ({100.0*(tb-ta)/tb:.1f}% saved)")
    if args.json:
        Path(args.json).write_text(json.dumps({"rows": rows, "passed": passed}, indent=1), encoding="utf-8")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
