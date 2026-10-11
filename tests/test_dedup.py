"""trimtok Phase 2 tests: cross-turn fold + hygiene + volatile. Run: python test_dedup.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dedup import (
    clear_session,
    compress_tool_result,
    expire_old_turns,
    fold_spans,
    hygiene,
    mask_old_turns,
    mask_summary,
    minify_json,
    normalize_volatile,
    note_turn,
    remember,
    toon_encode,
)


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1
check.failed = 0

SID = "test-session"

# 1. hygiene: trailing ws + blank runs
t, s = hygiene("line1   \nline2\t\n\n\n\nline3\n")
check("hygiene", t == "line1\nline2\n\nline3\n" and s > 0)

# 2. volatile: uuid/session/ts normalized
t2, s2 = normalize_volatile(
    'id 123e4567-e89b-12d3-a456-426614174000 at 20260917_214935_711ce4 on 2026-09-17 21:49:35'
)
check("volatile-uuid", "<UUID>" in t2 and "123e4567" not in t2)
check("volatile-sess", "<SESS>" in t2 and "20260917_214935" not in t2)
check("volatile-ts", "<TS>" in t2)

# 3. fold: second result folds spans from first
clear_session(SID)
code1 = "\n".join(f"def func_{i}(): return {i}" for i in range(40))
pad = "x" * 2100
r1 = code1 + "\n" + pad
out1, st1 = compress_tool_result("search_files", r1, session_id=SID)
check("first-indexed", st1["gate"] in ("ok", "reverted_gate"))
code2 = "\n".join(f"def func_{i}(): return {i}" for i in range(40)) + "\n" + "y" * 2100
out2, st2 = compress_tool_result("search_files", code2, session_id=SID)
check("fold-gate", st2["gate"] == "ok")
check("fold-pointer", "[tt:seen lines=" in out2)
check("fold-saved", st2["saved_chars"] > 500)

# 4. read_file stays verbatim but indexes
clear_session(SID + "2")
big_read = "content line\n" * 500 + "z" * 1000
out3, st3 = compress_tool_result("read_file", big_read, session_id=SID + "2")
check("read-verbatim", out3 == big_read and st3["gate"] == "passthrough_reference")
# ...and a later search can fold against it
out4, st4 = compress_tool_result(
    "search_files", ("content line\n" * 500) + "w" * 1000, session_id=SID + "2")
check("fold-against-read", st4["gate"] == "ok" and "[tt:seen" in out4)

# 5. sentinel passthrough
out5, st5 = compress_tool_result("search_files", out2, session_id=SID)
check("sentinel-skip", out5 == out2 and st5["gate"] == "passthrough_sentinel")

# 5b. repeat passthrough (rule 2): re-running r1 ships it full
o5b, s5b = compress_tool_result("search_files", r1, session_id=SID)
check("repeat-skip", o5b == r1 and s5b["gate"] == "passthrough_repeat")

# 6. small + non-str passthrough
o6, s6 = compress_tool_result("x", "tiny")
check("small-skip", s6["gate"] == "passthrough_small")
o7, s7 = compress_tool_result("x", None)
check("nonstr-skip", s7["gate"] == "passthrough_non_str")

# 7. never-inflate on unique content
clear_session(SID + "3")
uniq = "\n".join(f"totally-unique-{i}-qwerty-{i*7}" for i in range(120)) + "\n" + "q" * 500
o8, s8 = compress_tool_result("search_files", uniq, session_id=SID + "3")
check("never-inflate", len(o8) <= len(uniq))

# 8. determinism: same corpus state -> same fold shape. (Repeat inputs are
# passthrough by design — rule 2 — so compare equal-state runs, not re-runs.)
clear_session(SID + "4")
_, _ = compress_tool_result("search_files", r1, session_id=SID + "4")
a1, _ = compress_tool_result("search_files", code2, session_id=SID + "4")
clear_session(SID + "4")
_, _ = compress_tool_result("search_files", r1, session_id=SID + "4")
a2, _ = compress_tool_result("search_files", code2, session_id=SID + "4")
check("deterministic-shape", a1 == a2)

# 9. perf on 65KB
blob = ("2026-10-11 INFO tick\n" * 3000) + "ERROR final fail\n" + "z" * 2000
clear_session("perf")
compress_tool_result("terminal", blob, session_id="perf")
ms = sorted(
    compress_tool_result("terminal", blob, session_id="perf")[1].get("ms", 1e9)
    for _ in range(3))[1]
check("perf-p99", ms < 100)

# 10. minify lossless (default-on path): whole-doc JSON only, never partial
import json as _json
pretty = _json.dumps(
    {"a": [1, 2, {"b": "x" * 600}], "c": True, "pad": "p" * 900}, indent=2)
m, msaved = minify_json(pretty)
check("minify-saves", msaved > 0 and _json.loads(m) == _json.loads(pretty))
m2, ms2 = minify_json("not json at all " * 200)
check("minify-nonjson", m2 == "not json at all " * 200 and ms2 == 0)
m3, ms3 = minify_json('{"a": 1} trailing garbage ' * 100)
check("minify-partial-no", ms3 == 0)  # whole-doc only, never partial

# 11. TOON uniform (off by default, on with flag)
import json as _jj
rows = [{"id": i, "name": f"u{i}", "ok": True} for i in range(120)]
raw = _jj.dumps(rows)
clear_session("toon")
o_off, s_off = compress_tool_result("search_files", raw, session_id="toon")
check("toon-default-off", not s_off.get("toon_used", False))
clear_session("toon2")
o_on, s_on = compress_tool_result("search_files", raw, session_id="toon2", toon_enabled=True)
check("toon-on-saves", s_on.get("toon_used", False) and s_on.get("toon_saved", 0) > 0)
check("toon-legend", "[tt:legend]" in o_on)
# mixed dicts never TOON
clear_session("toon3")
mixed = _jj.dumps({"a": 1, "nested": {"x": [1, 2]}}) + "\n" + "z" * 2100
_, s_mix = compress_tool_result("search_files", mixed, session_id="toon3", toon_enabled=True)
check("toon-mixed-no", not s_mix.get("toon_used", False))
# toon_encode unit: non-uniform + nested rejected
t1, _ = toon_encode([{"a": 1}, {"b": 2}])
check("toon-nonuniform", t1 == "")
t2, _ = toon_encode([{"a": {"x": 1}}])
check("toon-nested-no", t2 == "")
typed, _ = toon_encode([
    {"value": None}, {"value": ""}, {"value": "true"},
    {"value": True}, {"value": 1},
])
check("toon-preserves-scalar-types", len(set(typed.splitlines()[1:])) == 5)
ambiguous, _ = toon_encode([{"a,b": 1}, {"a,b": 2}, {"a,b": 3}])
check("toon-rejects-ambiguous-field", ambiguous == "")

# 12. per-command budgets
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import _budget_for
check("budget-ls", _budget_for("ls -la", "text", 120) == 60)
check("budget-grep", _budget_for("rg foo", "search", 120) == 80)
check("budget-diff", _budget_for("whatever", "diff", 120) == 200)
check("budget-default", _budget_for("cat file", "text", 120) == 120)

# 13. pointer resolvability: every emitted pointer resolves to a stored raw
import re as _re
clear_session("resolve")
corpus_texts = {}
base = "\n".join(f"shared content line {i} with enough detail text abcdef" for i in range(60))
r_a = base + "\n" + "A" * 2200
r_b = base + "\n" + "B" * 2200
o_a, s_a = compress_tool_result("search_files", r_a, session_id="resolve")
corpus_texts[s_a.get("digest", "")] = r_a
o_b, s_b = compress_tool_result("search_files", r_b, session_id="resolve")
corpus_texts[s_b.get("digest", "")] = r_b
ptrs = _re.findall(r"\[tt:seen lines? retrieve:([0-9a-f]{12})\]", o_b)
check("resolve-has-pointers", len(ptrs) > 0)
# each pointer hash == the digest of a stored block (head marker digest)
stored = {s_a.get("digest"), s_b.get("digest")}
check("resolve-all", all(p in stored for p in ptrs))
# and the stored raw actually contains folded-away content
check("resolve-content", any("shared content line 30" in (corpus_texts.get(p) or "") for p in ptrs))

# 14. mask clock: note turns, expire old ones from the fold index
clear_session("mask")
for t in ("t1", "t2", "t3", "t4"):
    note_turn("x", f"digest-{t}", "mask", t)
    remember(f"unique body {t} " + "z" * 100, "mask", owner=f"digest-{t}")
n_exp = expire_old_turns("mask", keep_turns=2)
check("mask-expires", n_exp == 2)
# old content no longer folds (freshness), recent still does
from dedup import _seen as _dbg_seen
check("mask-index-shrunk", len(_dbg_seen("mask")["spans"]) == 2)
# placeholders for consumers
ph = mask_summary("line one\nline two\n" + "y" * 2100, "digest-t1", 2200)
check("mask-summary", ph.startswith("[tt:masked") and "retrieve:digest-t1" in ph)
# mask_old_turns maps digests -> placeholders against a CCR mirror
store = {"digest-t1": "line one\n" + "y" * 2100, "digest-t2": "small"}
# need turn registry entries first
clear_session("mask2")
note_turn("x", "digest-t1", "mask2", "old")
note_turn("x", "digest-t2", "mask2", "old")
note_turn("x", "digest-t3", "mask2", "new1")
note_turn("x", "digest-t4", "mask2", "new2")
note_turn("x", "digest-t5", "mask2", "new3")
mapped = mask_old_turns("mask2", "new3", keep_turns=2, store=store)
check("mask-maps", set(mapped) == {"digest-t1"})
# idempotent per turn
mapped2 = mask_old_turns("mask2", "new3", keep_turns=2, store=store)
check("mask-idempotent", mapped2 == {})

print(f"\n{check.failed} failures")
if __name__ == "__main__":
    sys.exit(1 if check.failed else 0)
