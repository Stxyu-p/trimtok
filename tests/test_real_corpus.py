"""Real-corpus regression tests: the compressor is measured on real payloads.

No mocked stats, no fake gate names, no hand-built config doubles. Every payload
under eval/corpus/ is a capture from a real file, log or command; the assertions
below are invariants a reader can verify from the returned (output, stats) pair.

Run: python tests/test_real_corpus.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Never let a test write production telemetry (~/.trimtok) or pollute the CCR
# store: point every store at a throwaway dir before importing the plugin.
os.environ["TRIMTOK_DATA_DIR"] = tempfile.mkdtemp(prefix="tt-realcorpus-")

import trimtok as tt  # noqa: E402
from trimtok.ccr import get as ccr_get  # noqa: E402
from trimtok.core import compress_terminal  # noqa: E402
from trimtok.signals import Deadline, is_important  # noqa: E402

CORPUS = _ROOT / "eval" / "corpus"
SENTINEL_RE = re.compile(r"#([0-9a-f]{6,32})")
GUARD_MS = 200.0

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else ' -- ' + detail}")
    if not ok:
        failures.append(name)


def run_payload(item: dict) -> tuple[str, dict]:
    """Drive the same entry point production uses for this payload."""
    content = item["content"]
    # Same split production uses: terminal output and tool results are different
    # pipelines with different contracts.
    if item["kind"] == "terminal":
        return compress_terminal(
            item["args"].get("cmd") or item["id"], content,
            session_id=item["id"], deadline=Deadline(GUARD_MS),
        )
    return tt.compress_tool_result(
        item["tool"], content, args=item["args"] or None,
        session_id=item["id"], deadline=Deadline(GUARD_MS),
    )


def resolvable(ref: str) -> bool:
    try:
        return bool(ccr_get(ref.lstrip("#")))
    except Exception:
        return False


def main() -> int:
    items = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CORPUS.glob("*.json"))]
    check("corpus-present", len(items) >= 30, f"{len(items)} payloads")

    total_before = total_after = 0
    for item in items:
        pid = item["id"]
        content: str = item["content"]
        t0 = time.perf_counter()
        try:
            out, st = run_payload(item)
        except Exception as exc:  # nothing may escape the compressor
            check(f"{pid}:no-raise", False, repr(exc))
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000
        check(f"{pid}:no-raise", isinstance(out, str))
        check(f"{pid}:never-inflates", len(out) <= len(content), f"{len(out)} > {len(content)}")
        check(f"{pid}:gate-carries-reason", st.get("gate") == "ok" or bool(st.get("reason")),
              f"gate={st.get('gate')} reason={st.get('reason')}")
        check(f"{pid}:under-guard", elapsed_ms < GUARD_MS * 3, f"{elapsed_ms:.0f}ms")

        total_before += len(content)
        total_after += len(out)
        if len(out) >= len(content):
            continue  # verbatim payloads cannot have lost anything

        # 1. Nothing is silently lost: every ref the compressor emitted resolves
        #    in the CCR and returns the original body a reader would need back.
        added_refs = set(SENTINEL_RE.findall(out)) - set(SENTINEL_RE.findall(content))
        bad = [r for r in added_refs if not resolvable(r)]
        check(f"{pid}:refs-resolve", not bad, f"dead refs {bad[:3]}")

        # 2. Dropped fault lines are announced, never swallowed. This is a
        #    terminal-log contract: in a data file (jsonl/config) `is_important`
        #    matches a field value, which is not a fault line.
        hidden_faults = [] if item["kind"] != "terminal" else [
            ln for ln in content.splitlines()
            if is_important(ln) and ln.strip() and ln.strip() not in out
        ]
        if hidden_faults:
            m = re.search(r"errors_hidden=(\d+)", out[:600])
            check(f"{pid}:faults-announced", bool(m) and int(m.group(1)) >= len(hidden_faults),
                  f"hidden={len(hidden_faults)} header={m.group(1) if m else 'none'}")

        # 3. Text folds keep real line anchors: a marker that says L{a}-L{b} must
        #    point inside the file and cover exactly the lines it claims.
        for s, e in re.findall(r"\[tt:prose L(\d+)-L(\d+)", out):
            check(f"{pid}:prose-range-sane", int(s) <= int(e) and int(e) <= len(content.splitlines()) + 1,
                  f"L{s}-L{e}")

        # 4. Markdown/YAML folds must keep their headings and top-level keys.
        if "file_structure" == st.get("kind"):
            src_lines = content.splitlines()
            must_keep = [
                ln.split("|", 1)[-1] for ln in src_lines
                if re.match(r"^ {0,3}#{1,3}\s", ln.split("|", 1)[-1]) and len(ln.split("|", 1)[-1]) > 4
            ][:40]
            missing = [ln for ln in must_keep if ln.strip() not in out]
            check(f"{pid}:headings-kept", not missing, f"{len(missing)} of {len(must_keep)} lost")

        # 5. Python skeletons keep every top-level symbol name.
        if "file_skeleton" == st.get("kind"):
            names = re.findall(r"^\s*(?:def|class)\s+(\w+)", content, re.M)
            missing = [n for n in names if n not in out]
            check(f"{pid}:symbols-kept", not missing, f"{missing[:5]}")

    reduction = 100.0 * (total_before - total_after) / total_before if total_before else 0.0
    print(f"\ncorpus reduction: {reduction:.2f}% of {total_before} chars")
    check("corpus-reduction-floor", reduction >= 60.0, f"{reduction:.2f}%")
    print(f"\n{checks - len(failures)}/{checks} checks passed, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
