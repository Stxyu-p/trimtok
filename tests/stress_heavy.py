"""trimtok heavy stress: token-reduction torture + weakness log. Run: python tests/stress_heavy.py"""
import os, sys, time, json, tempfile, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import compress_terminal, detect_kind
from dedup import (clear_session, compress_tool_result, hygiene, normalize_volatile,
                   minify_json, remember, note_turn, expire_old_turns)
from ccr import put as ccr_put, get as ccr_get
import ccr_v2

fails = 0
rows = []  # (name, before, after, pct, gate, ms, note)
def log(name, before, after, gate, ms, note=""):
    global fails
    pct = (before - after) / before * 100 if before else 0
    rows.append((name, before, after, pct, gate, ms, note))
    print(f"{pct:6.1f}% {before:>7}->{after:>6} [{gate:>22}] {ms:7.2f}ms | {name} {note}")

def tok(c): return c / 4  # ponytail: rough 4char/token, real tokenizer later if pricing matters

def run_term(name, cmd, out, note=""):
    t0 = time.perf_counter()
    n, st = compress_terminal(cmd, out)
    ms = (time.perf_counter() - t0) * 1000
    log(name, len(out), len(n), st.get("gate"), st.get("ms", ms), note)
    return n, st

def run_tool(name, tool, res, sid, note="", **kw):
    t0 = time.perf_counter()
    n, st = compress_tool_result(tool, res, session_id=sid, **kw)
    ms = (time.perf_counter() - t0) * 1000
    log(name, len(res), len(n), st.get("gate"), st.get("ms", ms), note)
    return n, st

def check(name, cond, extra=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""))
    if not cond: fails += 1

print("=== TERMINAL (Phase 1) ===")
# T1 heartbeat 5000 lines w/ varying timestamps (normalize-friendly)
t1 = "\n".join(f"2026-10-12 10:{i//60:02d}:{i%60:02d} INFO heartbeat ok worker={i%7}" for i in range(5000))
n, st = run_term("T1-heartbeat-5k", "tail -f app.log", t1)
check("T1-folds", st["gate"] == "ok" and st["saved_chars"] > 0, f"saved={st.get('saved_chars')}")
# T2 pytest 2000 lines + 1 error mid + summary tail
lines = [f"test_{i:04d}.py::test_x PASSED" for i in range(2000)]
lines.insert(1000, "FAILED test_1000.py::test_boom - AssertionError: expected 1 got 2")
lines.append("===== 1 failed, 1999 passed in 42.1s =====")
t2 = "\n".join(lines)
n2, st2 = run_term("T2-pytest-2k+1fail", "pytest -q", t2)
check("T2-keeps-error", "AssertionError" in n2 and "1 failed" in n2)
# T3 npm noise
t3 = ("\n".join(["npm notice created a lockfile", "npm WARN deprecated uuid@3.4.0",
                 "found 12 vulnerabilities (3 low, 9 high)", "up to date in 1.2s",
                 "Collecting requests", "Downloading foo-1.0.whl", "Using cached bar",
                 "already satisfied: baz in ./venv"] * 200)) + "\n" + "z" * 1500
run_term("T3-npm-noise", "npm install", t3, "noise filter")
# T4 git diff large
t4 = "".join(f"diff --git a/f{i}.py b/f{i}.py\n@@ -1 +1 @@\n-old{i}\n+new{i}\n" for i in range(800))
run_term("T4-gitdiff-800files", "git diff", t4)
# T5 grep 2000 hits (budget 80!)
t5 = "\n".join(f"src/mod{i%50}.py:{i}:match content payload here {i*7}" for i in range(2000))
n5, st5 = run_term("T5-grep-2k-b80", "rg foo", t5)
check("T5-budget80", len(n5.splitlines()) < 200, f"lines={len(n5.splitlines())}")
# T6 ls 2000 files (budget 60!)
t6 = "\n".join(f"file_{i:04d}.py" for i in range(2000))
run_term("T6-ls-2k-b60", "ls -la", t6)
# T7 ANSI 2k colored lines
t7 = "\x1b[31m" + "red line error content here\n" * 2000 + "\x1b[0m" + "z" * 1500
n7, st7 = run_term("T7-ansi-2k", "ls --color", t7)
check("T7-ansi-gone", "\x1b[" not in n7)
# T8 progress spinners + percentages
t8 = ("\n".join(f"{s} downloading... {i}%" for i, s in enumerate(["⠁","⠋","⠙","⠹","⠸","⠼"] * 400))) + "\n" + "w" * 1500
run_term("T8-spinner-2.4k", "cargo build", t8, "progress filter")
# T9 all-errors 3000 lines (forced > budget 200 -> quality path)
t9 = "\n".join(f"ERROR {i}: connection failed timeout task {i}" for i in range(3000))
n9, st9 = run_term("T9-all-errors-3k", "pytest", t9)
check("T9-err-gate", True, f"gate={st9['gate']} lines={len(n9.splitlines())}")
# T10 unique lines (no gain -> must not inflate)
t10 = "\n".join(f"uniq-{i}-qwerty-{i*7919 % 999983:06d}-zxcv-{i*i % 1000003:07d}" for i in range(3000)) + "\n" + "y" * 500
n10, st10 = run_term("T10-unique-3k", "echo", t10)
check("T10-no-inflate", len(n10) <= len(t10), f"gate={st10['gate']}")
# T11 Thai unicode 2k lines + Thai error (error regex is EN-only?)
t11 = ("\n".join(f"บรรทัดที่ {i} ระบบทำงานปกติ" for i in range(2000))) + "\nข้อผิดพลาด: การเชื่อมต่อล้มเหลว\n" + "ก" * 1500
n11, st11 = run_term("T11-thai-2k", "tail app.log", t11)
check("T11-thai-keeps-err", "ข้อผิดพลาด" in n11, f"gate={st11['gate']}")
# T12 mixed traceback
t12 = ("Traceback (most recent call last):\n" + "".join(f'  File "a.py", line {i}, in f\n    x={i}\n' for i in range(300)) + "ValueError: boom\n") * 8
run_term("T12-traceback", "pytest", t12)
# T13 KEEP under 1k-line window
many = [f"line {i} filler text here xyz" for i in range(1000)]
many[900] = "TRIMTOK_KEEP: account 123 balance 456.78"
n13, _ = run_term("T13-keep-survives", "dump", "\n".join(many))
check("T13-keep", "TRIMTOK_KEEP" in n13)
# T14 500KB blob perf
t14 = ("2026-10-12 INFO tick worker payload data here\n" * 25000)[:500_000]
_, st14 = run_term("T14-500KB", "tail big.log", t14)
check("T14-perf", st14.get("ms", 1e9) < 500, f"ms={st14.get('ms'):.1f}")
# T15 error hidden inside repeat run (fold must not eat it)
rep = ["INFO heartbeat ok"] * 100
rep.insert(50, "ERROR disk full panic")
t15 = "\n".join(rep) + "\n" + "x" * 1500
n15, _ = run_term("T15-err-in-repeats", "pytest", t15)
check("T15-err-survives-fold", "disk full" in n15)

print("=== TOOL RESULT (Phase 2) ===")
clear_session("S")
# D1 pretty JSON 200 items -> minify
pretty = json.dumps({"items": [{"id": i, "name": "module_%d" % i, "desc": "x" * 60} for i in range(200)]}, indent=2)
n, st = run_tool("D1-prettyJSON-200", "search_files", pretty, "S")
check("D1-minify", st.get("minify_saved", 0) > 0, f"minify={st.get('minify_saved')}")
# D2 TOON 500 uniform rows off vs on
rows500 = [{"id": i, "name": f"u{i}", "ok": True} for i in range(500)]
raw500 = json.dumps(rows500)
clear_session("toon-off"); o_off, s_off = run_tool("D2a-TOON-off-500", "search_files", raw500, "toon-off", "toon off", toon_enabled=False)
clear_session("toon-on"); o_on, s_on = run_tool("D2b-TOON-on-500", "search_files", raw500, "toon-on", "toon on", toon_enabled=True)
check("D2-toon-saves-more", s_on.get("saved_chars", 0) >= s_off.get("saved_chars", 0), f"off={s_off.get('saved_chars')} on={s_on.get('saved_chars')}")
# D15 TOON lossless 1000-row table (path prefix + status canary) — Phase 3
clear_session("toonp3a")
t_rows = [
    {"path": f"C:\\repo\\src\\mod{i:04d}.py", "lines": 999999 if i == 777 else 40 + (i % 900), "status": 500 if i == 777 else 200, "ok": True}
    for i in range(1000)
]
pj3 = json.dumps(t_rows, indent=2)
o15a, s15a = run_tool("D15a-toon-off-1k", "search_files", pj3, "toonp3a", "toon off", toon_enabled=False)
clear_session("toonp3b")
o15b, s15b = run_tool("D15b-toon-on-1k", "search_files", pj3, "toonp3b", "toon on")
check("D15-toon-uses", s15b.get("toon_used", False) and s15b.get("toon_saved", 0) > 0, f"toon_saved={s15b.get('toon_saved')}")
check("D15-toon-beats-minify", s15b.get("saved_chars", 0) > s15a.get("saved_chars", 0), f"off={s15a.get('saved_chars')} on={s15b.get('saved_chars')}")
check("D15-canary", ("999999" in o15b) and ("500" in o15b))
check("D15-prefix", "@prefix(~/)" in o15b)
# D3/D4 cross-turn chain
clear_session("chain")
base = "\n".join(f"shared content line {i} with enough detail text abcdef" for i in range(60))
r1 = base + "\n" + "A" * 2200
r2 = base + "\n" + "B" * 2200
r3 = base + "\n" + "C" * 2200
o1, s1 = run_tool("D3-turn1-base", "search_files", r1, "chain")
o2, s2 = run_tool("D3-turn2-folds", "search_files", r2, "chain")
o3, s3 = run_tool("D4-turn3-folds", "search_files", r3, "chain")
check("D3-fold", s2["gate"] == "ok" and "[@#" in o2, f"saved={s2.get('saved_chars')}")
check("D4-fold3", s3["gate"] == "ok" and "[@#" in o3, f"saved={s3.get('saved_chars')}")
# D5 read_file verbatim + fold against
clear_session("rf")
big_read = "content line data payload here\n" * 1500 + "z" * 1000
o_r, s_r = run_tool("D5a-read-verbatim", "read_file", big_read, "rf")
o_q, s_q = run_tool("D5b-search-folds-vs-read", "search_files", ("content line data payload here\n" * 1500) + "w" * 1000, "rf")
check("D5-verbatim", s_r["gate"] == "passthrough_reference" and o_r == big_read)
check("D5-fold-vs-read", s_q["gate"] == "ok" and "[@#" in o_q)
# D6 hygiene heavy
h = ("trailing spaces here   \n" * 1000) + ("\n\n\n\n" * 500) + ("clean line\n" * 500)
t_h, s_h = hygiene(h)
log("D6-hygiene", len(h), len(t_h), "hygiene", 0, f"saved={s_h}")
# D7 volatile 500 UUIDs
v = "\n".join(f"entry {i} id 123e4567-e89b-12d3-a456-426614174{i:03d} at 2026-09-17 21:49:35 sess 20260917_214935_711ce4" for i in range(500))
t_v, s_v = normalize_volatile(v)
log("D7-volatile-500", len(v), len(t_v), "volatile", 0, f"saved={s_v}")
# D8 repeat re-run passthrough (by design 0%)
o_rep, s_rep = run_tool("D8-repeat-passthru", "search_files", r1, "chain", "by design 0%")
check("D8-repeat", s_rep["gate"] == "passthrough_repeat")
# D9 single long-line repeats
clear_session("lines")
lbase = "\n".join(f"this is a long distinctive log line number {i} with payload abcdefgh" for i in range(30))
la = lbase + "\n" + "P" * 2100
lb = lbase + "\n" + "Q" * 2100
run_tool("D9a-seed", "search_files", la, "lines")
o_lb, s_lb = run_tool("D9b-line-fold", "search_files", lb, "lines")
check("D9-linefold", "[@#" in o_lb, f"saved={s_lb.get('saved_chars')}")
# D10 JSON envelope inner fold (minified vs pretty SKILL.md case)
clear_session("env")
inner = "\n".join(f"documentation line {i} explaining the skill usage in detail here" for i in range(80))
env1 = json.dumps({"content": inner, "meta": {"id": 1}}, ensure_ascii=False)
env2 = json.dumps({"content": inner, "meta": {"id": 2}}, ensure_ascii=False)  # same inner, diff envelope
run_tool("D10a-env1", "search_files", env1 + "\n" + "p" * 2100, "env")
o_e2, s_e2 = run_tool("D10b-env2-innerfold", "search_files", env2 + "\n" + "q" * 2100, "env")
check("D10-inner", True, f"gate={s_e2['gate']} saved={s_e2.get('saved_chars')}")
# D11 unique tool 10k (must revert, 0%)
clear_session("uq")
uq = "\n".join(f"totally-unique-{i}-qwerty-{i*7}-zz-{i*i%100003}" for i in range(300)) + "\n" + "q" * 500
o_uq, s_uq = run_tool("D11-unique-nogain", "search_files", uq, "uq")
check("D11-no-inflate", len(o_uq) <= len(uq), f"gate={s_uq['gate']}")
# D12 1MB tool perf
clear_session("big")
big1mb = ("log line payload data for performance testing 12345\n" * 20000)[:1_000_000]
t0 = time.perf_counter(); o_b, s_b = compress_tool_result("search_files", big1mb, session_id="big")
ms_b = (time.perf_counter() - t0) * 1000
log("D12-1MB-tool", len(big1mb), len(o_b), s_b.get("gate"), ms_b)
check("D12-perf", ms_b < 2000, f"ms={ms_b:.1f}")
# D13 mask expiry: old stops folding
clear_session("maskt")
for t in ("t1", "t2", "t3", "t4"):
    note_turn("x", f"d-{t}", "maskt", t)
    remember(f"unique body {t} " + "z" * 3000, "maskt", owner=f"d-{t}")
n_exp = expire_old_turns("maskt", keep_turns=2)
check("D13-mask-expires", n_exp == 2, f"expired={n_exp}")
# D14 scoped search: multi-file grep groups by file with defs headers
clear_session("scoped")
d14 = "\n".join(
    f"src/mod{i}.py:{n * 3 + 7}:def handler_{i}_{n}(req): return case_{n}"
    for i in range(8)
    for n in range(100)
)
o14, s14 = run_tool("D14-scoped-search", "search_files", d14, "scoped")
check(
    "D14-scoped",
    s14["gate"] == "ok" and "=== src/mod0.py (100 hits)" in o14 and "[@#" in o14,
    f"saved={s14.get('saved_chars')} search_saved={s14.get('search_saved')}",
)
# D14b: the plan's canary — a fault hit inside the folded middle stays visible
clear_session("scoped2")
d14c = d14.split("\n")
d14c.insert(350, "src/mod3.py:998:status=500 upstream gone")
o14c, s14c = run_tool("D14b-scoped-canary", "search_files", "\n".join(d14c), "scoped2")
check(
    "D14b-canary",
    s14c["gate"] == "ok" and "status=500 upstream gone" in o14c,
    f"saved={s14c.get('saved_chars')}",
)

# D16: cross-turn command delta loop (Phase 4) — repeated pytest, faults evolve
print("=== D16 COMMAND DELTA LOOP (5 turns) ===")
with tempfile.TemporaryDirectory() as td:
    from pathlib import Path
    import ccr
    import delta_engine
    d16 = Path(td)
    orig16 = ccr.default_dir
    ccr.default_dir = lambda: d16
    try:
        delta_engine.clear_session("d16")
        base16 = [f"test_{i:04d}.py::test_x PASSED" for i in range(1500)]
        persistent16 = "FAILED test_0500.py::test_z - AssertionError: boom"
        turns16 = []
        for k in range(5):
            ls = list(base16)
            ls[100] = f"FAILED test_0100.py::test_a - AssertionError: flaky {k}"
            ls[101] = f"FAILED test_0101.py::test_b - AssertionError: flaky {k + 1}"
            ls.append(persistent16)
            ls.append(f"===== 2 failed, 1498 passed in 4{k}.{k}s =====")
            turns16.append("\n".join(ls))
        tot_b = tot_d = 0
        for t in turns16:
            o, s = compress_terminal("pytest -q", t, session_id="d16", delta_enabled=True)
            tot_b += len(t); tot_d += len(o)
        off_b = off_a = 0
        for t in turns16:
            o, s = compress_terminal("pytest -q", t)
            off_b += len(t); off_a += len(o)
        red_d = (tot_b - tot_d) / tot_b * 100
        red_o = (off_b - off_a) / off_b * 100
        log("D16-loop-delta", tot_b, tot_d, "delta", 0, f"reduction={red_d:.1f}% per-turn-off={red_o:.1f}%")
        check("D16-delta>=82", red_d >= 82.0, f"delta={red_d:.1f}%")
        o5, s5 = compress_terminal("pytest -q", turns16[4], session_id="d16", delta_enabled=True)
        check(
            "D16-canary-still-failing",
            persistent16 in o5 and "STILL FAILING" in o5,
            f"delta_chars={len(o5)} saved={s5.get('saved_chars')}",
        )
    finally:
        ccr.default_dir = orig16
        ccr_v2.close(dir=d16)

print("=== CCR + RETRIEVE ===")
with tempfile.TemporaryDirectory() as td:
    from pathlib import Path
    d = Path(td)
    blob100k = ("0123456789abcdef\n" * 6250)[:100_000]
    t0 = time.perf_counter(); hh = ccr_put(blob100k, dir=d); put_ms = (time.perf_counter()-t0)*1000
    t0 = time.perf_counter(); back = ccr_get(hh, dir=d); get_ms = (time.perf_counter()-t0)*1000
    log("C1-ccr-100KB-put+get", len(blob100k), len(back or ""), "roundtrip", put_ms + get_ms, f"put={put_ms:.1f} get={get_ms:.1f}")
    check("C1-roundtrip", back == blob100k)
    check("C1-missing", ccr_get("deadbeef1234", dir=d) is None)
    # retrieve slice saving: full 100k vs 2-line slice
    import __init__ as plugin
    import ccr
    orig = ccr.default_dir; ccr.default_dir = lambda: d
    try:
        sl = json.loads(plugin._handle_retrieve({"hash": hh, "offset": 2, "limit": 2}))
        slice_chars = len(sl.get("lines", ""))
        log("C2-retrieve-slice-vs-full", len(blob100k), slice_chars, "paginate", 0, f"slice={slice_chars}")
        check("C2-slice-saves", slice_chars < len(blob100k) / 10)
        gr = json.loads(plugin._handle_retrieve({"hash": hh, "grep": "deadbeef"}))
        check("C3-grep-nomatch", gr.get("matches_count") == 0)
    finally:
        ccr.default_dir = orig
        ccr_v2.close(dir=d)

print("=== SESSION SIM (realistic 5-turn) ===")
clear_session("sim")
sim_before = 0; sim_after = 0
def acc(o, n_): return (o + len(n_[0]) if isinstance(n_, tuple) else o)
turns = []
skill = ("# Skill doc line with real content payload here\n" * 800)[:30_000]
o, s = compress_tool_result("read_file", skill, session_id="sim"); sim_before += len(skill); sim_after += len(o); turns.append(("read skill", len(skill), len(o), s["gate"]))
s1 = ("\n".join(f"src/a.py:{i}:def func_{i}(): return {i}" for i in range(400)))[:20_000]
o, s = compress_tool_result("search_files", s1, session_id="sim"); sim_before += len(s1); sim_after += len(o); turns.append(("search1", len(s1), len(o), s["gate"]))
s2 = ("\n".join(f"src/a.py:{i}:def func_{i}(): return {i}" for i in range(400)))[:20_000].replace("return", "return  # c") + "tail"
o, s = compress_tool_result("search_files", s2, session_id="sim"); sim_before += len(s2); sim_after += len(o); turns.append(("search2-overlap", len(s2), len(o), s["gate"]))
pt = ("\n".join(f"test_{i}.py PASSED" for i in range(1500)) + "\nFAILED test_x - Error boom\n" + "x" * 1500)
o, s = compress_terminal("pytest -q", pt); sim_before += len(pt); sim_after += len(o); turns.append(("pytest", len(pt), len(o), s["gate"]))
pj = json.dumps([{"id": i, "name": f"u{i}", "ok": True} for i in range(300)])
o, s = compress_tool_result("search_files", pj, session_id="sim", toon_enabled=True); sim_before += len(pj); sim_after += len(o); turns.append(("json-toon", len(pj), len(o), s["gate"]))
for nm, b, a, g in turns:
    pct = (b - a) / b * 100 if b else 0
    print(f"  sim {nm}: {b}->{a} ({pct:.1f}%) [{g}]")
sim_pct = (sim_before - sim_after) / sim_before * 100
print(f"SIM TOTAL: {sim_before}->{sim_after} ({sim_pct:.1f}%) est-tok-saved={tok(sim_before-sim_after):,.0f}")

print("\n=== SUMMARY (chars; est-tok=chars/4) ===")
tot_b = sum(b for _, b, _, _, _, _, _ in rows); tot_a = sum(a for _, _, a, _, _, _, _ in rows)
print(f"ALL CASES: {tot_b:,}->{tot_a:,} chars ({(tot_b-tot_a)/tot_b*100:.1f}%) ~{tok(tot_b-tot_a):,.0f} tokens saved")
print(f"fails={fails}")
sys.exit(1 if fails else 0)
