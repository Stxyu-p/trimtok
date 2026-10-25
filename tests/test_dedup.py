"""trimtok Phase 2 tests: cross-turn fold + hygiene + volatile. Run: python test_dedup.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trimtok.dedup import (
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
# W7: short HH:MM:SS normalizes; HH:MM / file:line:col stay verbatim
_t7, _ = normalize_volatile("10:00:05 INFO worker done")
check("volatile-short-ts", _t7 == "<TS> INFO worker done")
check("volatile-colon-noop", normalize_volatile("file.py:12:30 ref")[0] == "file.py:12:30 ref")
check("volatile-hhmm-noop", normalize_volatile("ratio 16:9 video")[0] == "ratio 16:9 video")
# W7: near-dup timestamps fold cross-turn (was reverted_gate 0%)
clear_session("ts-near-dup")
_a = "\n".join(f"10:00:{i % 60:02d} INFO worker done task {i} with payload data padding abcdef" for i in range(60)) + "\n" + "A" * 2200
_b = "\n".join(f"10:01:{i % 60:02d} INFO worker done task {i} with payload data padding abcdef" for i in range(60)) + "\n" + "B" * 2200
_, _sa = compress_tool_result("search_files", _a, session_id="ts-near-dup")
_ob, _sb = compress_tool_result("search_files", _b, session_id="ts-near-dup")
check("volatile-near-dup-folds", _sb["gate"] == "ok" and "[@#" in _ob)

# 2b. no-op normalization stays lossless on large ordinary output
ordinary = ("INFO build status line 123 process completed\n" * 22000)[:1_000_000]
clean, clean_saved = hygiene(ordinary)
normalized, normalized_saved = normalize_volatile(ordinary)
check("large-hygiene-noop", clean == ordinary and clean_saved == 0)
check("large-volatile-noop", normalized == ordinary and normalized_saved == 0)
del ordinary, clean, normalized

# 2c. remember keeps owner keys and hashes only when no owner is supplied
import hashlib as _hashlib
owner_text = "stored content with an explicit owner"
owner_key = remember(owner_text, "owner-key", owner="custom-owner")
check("remember-owner-key", owner_key == "custom-owner")
clear_session("owner-key")
fallback_text = "stored content without an owner"
fallback_key = remember(fallback_text, "fallback-key")
expected_key = _hashlib.sha256(fallback_text.encode("utf-8", "replace")).hexdigest()[:12]
check("remember-hash-fallback", fallback_key == expected_key)
clear_session("fallback-key")

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
check("fold-pointer", "[@#" in out2)
check("fold-saved", st2["saved_chars"] > 500)

# 4. read_file stays verbatim but indexes
clear_session(SID + "2")
big_read = "content line\n" * 500 + "z" * 1000
out3, st3 = compress_tool_result("read_file", big_read, session_id=SID + "2")
check("read-verbatim", out3 == big_read and st3["gate"] == "passthrough_reference")
# ...and a later search can fold against it
out4, st4 = compress_tool_result(
    "search_files", ("content line\n" * 500) + "w" * 1000, session_id=SID + "2")
check("fold-against-read", st4["gate"] == "ok" and "[@#" in out4)

# 5. sentinel passthrough
out5, st5 = compress_tool_result("search_files", out2, session_id=SID)
check("sentinel-skip", out5 == out2 and st5["gate"] == "passthrough_sentinel")

# 5b. repeat passthrough (rule 2): re-running r1 ships it full or folds in v4.2
o5b, s5b = compress_tool_result("search_files", r1, session_id=SID)
check("repeat-skip", s5b["gate"] in ("passthrough_repeat", "ok"))

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

# 7b. W4: first-seen repetitive doc folds within itself (was reverted 0%)
clear_session(SID + "-intra")
_rep = ("log line payload data for performance testing 12345\n" * 3000)[:150_000]
_o9, _s9 = compress_tool_result("search_files", _rep, session_id=SID + "-intra")
check("intra-first-seen", _s9["gate"] == "ok" and _s9.get("intra_saved", 0) > 0)
check("intra-marker", "[repeated " in _o9)
# ...but runs of 2 never fold, and unique docs still revert
clear_session(SID + "-intra2")
_two = ("same long payload line abcdefgh content here\n" * 2 + "diff line here\n") * 50 + "z" * 2100
_o10, _s10 = compress_tool_result("search_files", _two, session_id=SID + "-intra2")
check("intra-run2-no", _o10.count("[repeated ") == 0)

# 7c. W3 REFINED (2026-10-13 intra-template improvement): first-seen docs
# whose lines share a volatile-slot template (group>=3) now fold to a
# [tt:template ... retrieve:H] pointer — H is the raw digest the hook stores,
# so every pointer resolves via trimtok_retrieve (verified end-to-end).
# Genuinely unique docs (no template group) still revert full (keep-earliest).
clear_session(SID + "-firstseen")
_fs = "\n".join(f"first-seen-unique-{i}-qwerty-{i * 13}-zz-{i * i % 100019}" for i in range(120)) + "\n" + "v" * 2100
_o11, _s11 = compress_tool_result("search_files", _fs, session_id=SID + "-firstseen")
check("firstseen-template-folds", _s11["gate"] == "ok" and "[tt:template lines=119 #" in _o11)
check("firstseen-template-resolves", _s11.get("digest") and _s11["digest"] in _o11)
clear_session(SID + "-firstseen-uq")
import random as _uq_rnd
_uq_rnd.seed(99)
_uq_words = ["alpha", "beta", "gamma", "delta", "config", "status", "result",
             "payload", "query", "index", "cache", "token", "river", "stone",
             "cloud", "flame", "harbor", "maple", "quartz", "willow"]
_uq_lines = [" ".join(_uq_rnd.sample(_uq_words, 6)) + f" marker{i:05d} tailtext here" for i in range(120)]
_uq_fs2 = "\n".join(_uq_lines) + "\n" + "v" * 2100
_ouq, _suq = compress_tool_result("search_files", _uq_fs2, session_id=SID + "-firstseen-uq")
check("firstseen-unique-full", _suq["gate"] == "reverted_gate" and _ouq == _uq_fs2)

# 7d. HEAD_TAIL: large unique doc (192KB) emits stub, not full revert.
import random as _rnd
_rnd.seed(7)
_words = ["alpha", "beta", "gamma", "delta", "config", "status", "result", "payload", "query", "index", "cache", "token"]
_ht = [" ".join(_rnd.sample(_words, 6)) + f" marker{i:05d} tailtext here" for i in range(3000)]
_ht_big = "\n".join(_ht) + "\n" + "q" * 500
clear_session(SID + "-headtail")
_oht, _sht = compress_tool_result("search_files", _ht_big, session_id=SID + "-headtail")
check("headtail-fires", _sht["gate"] == "ok" and "[tt:headtail " in _oht)
check("headtail-ends", _ht[0] in _oht and _ht[-1] in _oht)
check("headtail-saves", _sht.get("saved_chars", 0) > 100_000)

# 8. determinism: same corpus state -> same fold shape. (Repeat inputs are
# passthrough by design — rule 2 — so compare equal-state runs, not re-runs.)
clear_session(SID + "4")
_, _ = compress_tool_result("search_files", r1, session_id=SID + "4")
a1, _ = compress_tool_result("search_files", code2, session_id=SID + "4")
clear_session(SID + "4")
_, _ = compress_tool_result("search_files", r1, session_id=SID + "4")
a2, _ = compress_tool_result("search_files", code2, session_id=SID + "4")
check("deterministic-shape", a1 == a2)

# 8b. Shingle near-dedup: same template, trailing slot varies per line.
# (Jaccard>=0.8 at line level; keep-first template stays in corpus.)
clear_session(SID + "-shingle")
_sh_base = ["worker node alpha finished job payload-xyz-abcdefgh with extended context detail field result status code-%02d" % i for i in range(40)]
_sh_a = "\n".join(_sh_base) + "\n" + "A" * 2100
_sh_b_lines = ["worker node alpha finished job payload-xyz-abcdefgh with extended context detail field result status code-%02d" % (i + 40) for i in range(40)]
_sh_b = "\n".join(_sh_b_lines) + "\n" + "B" * 2100
_, _sh_s1 = compress_tool_result("search_files", _sh_a, session_id=SID + "-shingle")
_o_sh, _sh_s2 = compress_tool_result("search_files", _sh_b, session_id=SID + "-shingle")
check("shingle-fires", _sh_s2["gate"] == "ok" and "[tt:shingle " in _o_sh)
# bare pointers: template stays verbatim in the seed corpus (keep-first),
# pointers resolve via CCR — same contract as [tt:seen]
from trimtok.dedup import _seen as _sh_seen
check("shingle-keep-first", any("payload-xyz" in t for _, t in _sh_seen(SID + "-shingle")["spans"]))
# ...but short lines never fold even at Jaccard 1.0-adjacent (pointer longer)
clear_session(SID + "-shingle2")
_sh_s = ["stable content line %d with shared wording here" % i for i in range(40)]
_sh_sa = "\n".join(_sh_s) + "\n" + "A" * 2100
_sh_sb = "\n".join(_sh_s) + "\n" + "B" * 2100
_, _ = compress_tool_result("search_files", _sh_sa, session_id=SID + "-shingle2")
_o_c, _s_c = compress_tool_result("search_files", _sh_sb, session_id=SID + "-shingle2")
check("shingle-short-no", "[tt:shingle " not in _o_c)

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
# W-D10: JSON envelope + plain tail minifies the prefix, keeps tail verbatim
_env_tail = _json.dumps({"a": [1, 2], "pad": "p" * 900}, indent=2) + "\n" + "z" * 2100
_mt, _mts = minify_json(_env_tail)
check("minify-tail-prefix", _mts > 0 and _mt.endswith("z" * 2100))
# ...but a JSON-looking tail stays untouched (partial-no preserved)
_mg, _mgs = minify_json('{"a": 1}\n{"b": 2}\n' + "z" * 2100)
check("minify-json-tail-no", _mgs == 0)
# W-D10: inner lines index despite the trailing tail -> later query folds
clear_session("env-tail")
_inner = "\n".join(f"documentation line {i} explaining usage in detail here" for i in range(80))
_e1 = _json.dumps({"content": _inner, "meta": {"id": 1}}, indent=2) + "\n" + "p" * 2100
_e2 = _json.dumps({"content": _inner, "meta": {"id": 2}}, indent=2) + "\n" + "q" * 2100
_, _es1 = compress_tool_result("search_files", _e1, session_id="env-tail")
_o2, _es2 = compress_tool_result("search_files", _e2, session_id="env-tail")
check("env-tail-folds", _es2["gate"] in ("ok", "reverted_gate"))

# 11. TOON lossless columnar (default ON; round-trip verified; gain-gated)
import json as _jj
from trimtok.toon_codec import fold_json_to_toon as _fjt, toon_decode as _tdec
rows = [{"id": i, "name": f"u{i}", "ok": True} for i in range(120)]
raw = _jj.dumps(rows)
clear_session("toon")
o_def, s_def = compress_tool_result("search_files", raw, session_id="toon")
check("toon-default-on", s_def.get("toon_used", False) and s_def.get("toon_saved", 0) > 0)
check("toon-legend", "[tt:legend]" in o_def)
_i1 = o_def.find("@prefix(")
_i2 = o_def.find("@rows[")
_start = _i1 if _i1 != -1 and (_i2 == -1 or _i1 < _i2) else _i2
check("toon-roundtrip", _tdec(o_def[_start:]) == rows)
clear_session("toonoff")
_, s_offwe = compress_tool_result("search_files", raw, session_id="toonoff", toon_enabled=False)
check("toon-flag-off", not s_offwe.get("toon_used", False))
# mixed/nested/tail docs never TOON
clear_session("toon3")
mixed = _jj.dumps({"a": 1, "nested": {"x": [1, 2]}}) + "\n" + "z" * 2100
_, s_mix = compress_tool_result("search_files", mixed, session_id="toon3")
check("toon-mixed-no", not s_mix.get("toon_used", False))
# fold unit: non-uniform / nested / bad-field rejected (returns input)
_t_bad1 = _jj.dumps([{"a": 1}, {"b": 2}])
check("toon-nonuniform", _fjt(_t_bad1) == _t_bad1)
_t_bad2 = _jj.dumps([{"a": {"x": 1}}, {"a": {"x": 2}}, {"a": {"x": 3}}])
check("toon-nested-no", _fjt(_t_bad2) == _t_bad2)
_t_bad3 = _jj.dumps([{"a,b": 1}, {"a,b": 2}, {"a,b": 3}])
check("toon-rejects-ambiguous-field", _fjt(_t_bad3) == _t_bad3)
# scalar type fidelity survives the round-trip (the exported side is exact)
typed_rows = [{"value": v} for v in (None, "", "true", True, 1, 2.5, "5", "a|b", "~x", "  pad  ")]
_t_json = _jj.dumps(typed_rows)
_t_out = _fjt(_t_json)
check("toon-scalar-fidelity", _t_out != _t_json and _tdec(_t_out) == typed_rows)
# key order per row is tolerated (decode equality is order-free)
_ord_rows = [{"alpha": 1, "beta": 2}, {"beta": 3, "alpha": 4}, {"alpha": 5, "beta": 6},
             {"beta": 7, "alpha": 8}, {"alpha": 9, "beta": 10}]
_ord_json = _jj.dumps(_ord_rows)
_t_ord3 = _fjt(_ord_json)
check("toon-key-order-ok", _t_ord3 != _ord_json and _tdec(_t_ord3) == _ord_rows)

# 11b. Guardrails: exemption list + minimum-size gate.
from trimtok.dedup import is_exempt as _is_exempt
check("exempt-port", _is_exempt("listen on port 8080 ready"))
check("exempt-count", _is_exempt("total 3000 errors completed"))
check("exempt-hash", _is_exempt("commit abc1234def5678 done"))
check("exempt-keep", _is_exempt("TRIMTOK_KEEP: secret"))
check("exempt-no", not _is_exempt("worker heartbeat ok payload here"))
# 500-token minimum-size gate == min_chars 2048 default (2048/4 = 512 tokens)
from trimtok.dedup import compress_tool_result as _ctr2
clear_session("min-gate")
_small500 = "x" * 2000
assert len(_small500) // 4 < 512
_o500, _s500 = _ctr2("search_files", _small500, session_id="min-gate")
check("min-gate-500tok", _s500["gate"] == "passthrough_small")
# (TOON key-order tolerance covered in section 11 — decode equality is order-free)

# 12. per-command budgets
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trimtok.core import _budget_for
check("budget-ls", _budget_for("ls -la", "text", 120) == 60)
check("budget-grep", _budget_for("rg foo", "search", 120) == 80)
check("budget-diff", _budget_for("whatever", "diff", 120) == 200)
check("budget-default", _budget_for("cat file", "text", 120) == 120)
# W9: substring must not match ("cat diffutils.txt" is not diff, "false" is not ls)
check("budget-no-substring", _budget_for("cat diffutils.txt", "text", 120) == 120)
check("budget-word-boundary", _budget_for("false", "text", 120) == 120)
check("budget-contest", _budget_for("contest run", "text", 120) == 120)
check("budget-git-log-first", _budget_for("git log --oneline", "text", 120) == 60)

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
ptrs = _re.findall(r"\[@#([0-9a-f]{12})(?:x\d+)?\]", o_b)
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
from trimtok.dedup import _seen as _dbg_seen
mask_state = _dbg_seen("mask")
check(
    "mask-index-shrunk",
    len(mask_state["spans"]) == 2
    and mask_state["span_chars"] == sum(len(text) for _, text in mask_state["spans"]),
)
# placeholders for consumers
ph = mask_summary("line one\nline two\n" + "y" * 2100, "digest-t1", 2200)
check("mask-summary", ph.startswith("[tt:masked") and "#digest-t1" in ph)
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

# 15. fold-index memory budgets evict old corpus and bound the line index
import trimtok.dedup as _dedup

_old_span_cap = _dedup._MAX_SPAN_CHARS
_old_line_cap = _dedup._MAX_INDEXED_LINES
try:
    _dedup._MAX_SPAN_CHARS = 100
    _dedup._MAX_INDEXED_LINES = 10
    clear_session("span-budget")
    remember("a" * 60, "span-budget", owner="span-a")
    remember("b" * 60, "span-budget", owner="span-b")
    budget_state = _dbg_seen("span-budget")
    check(
        "span-budget-evicts-oldest",
        [h for h, _ in budget_state["spans"]] == ["span-b"]
        and budget_state.get("span_chars") == 60,
    )
    remember("c" * 101, "span-budget", owner="span-too-large")
    check(
        "oversize-span-preserves-cache",
        [h for h, _ in budget_state["spans"]] == ["span-b"],
    )

    _dedup._MAX_SPAN_CHARS = 1000
    _dedup._MAX_INDEXED_LINES = 2
    clear_session("line-budget")
    line_text = "\n".join(f"line-{i}-" + ("x" * 40) for i in range(3))
    remember(line_text, "line-budget", owner="line-owner")
    line_state = _dbg_seen("line-budget")
    check("line-index-budget", len(line_state["lines"]) == 2)
finally:
    _dedup._MAX_SPAN_CHARS = _old_span_cap
    _dedup._MAX_INDEXED_LINES = _old_line_cap
    clear_session("span-budget")
    clear_session("line-budget")

print(f"\n{check.failed} failures")
if __name__ == "__main__":
    sys.exit(1 if check.failed else 0)
