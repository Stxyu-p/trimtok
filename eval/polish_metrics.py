"""Objective baseline for the polish rounds: LOC, dead defs, dupes, timing."""
import ast, re, sys, json, time, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = sorted(p for p in (ROOT / "trimtok").glob("*.py"))
ALL = MODS + sorted((ROOT / "tests").glob("*.py")) + [ROOT / "trimtok_mcp.py", ROOT / "trimtok_cli.py"]
ALL = [p for p in ALL if p.exists()]

def files():
    return {p: p.read_text(encoding="utf-8", errors="replace") for p in ALL}

def defs(src):
    out = []
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((n.name, n.lineno, n.end_lineno - n.lineno + 1))
    return out

def main():
    f = files()
    corpora = "\n".join(f.values())
    total = dead = 0
    dead_rows = []
    for p in MODS:
        src = f[p]
        for name, ln, size in defs(src):
            total += 1
            if name.startswith("__"):
                continue
            uses = len(re.findall(rf"\b{re.escape(name)}\b", corpora))
            exported = name in src.split("__all__")[-1][:400] if "__all__" in src else False
            if uses <= 1 and not exported:
                dead += 1
                dead_rows.append((p.name, name, ln, size))
    loc = {p.name: len(s.split("\n")) for p, s in f.items()}
    import_blocks = sum(len(re.findall(r"try:\s*\n\s+from \.", s)) for p, s in f.items() if p.parent.name == "trimtok")
    sys.path.insert(0, str(ROOT))
    import json as _j
    from trimtok.core import compress_terminal
    from trimtok.signals import Deadline
    import glob as _g
    before = after = 0
    ms = []
    for fn in sorted(_g.glob(str(ROOT / "eval/corpus/*.json"))):
        it = _j.loads(Path(fn).read_text(encoding="utf-8"))
        if it["kind"] != "terminal":
            continue
        t = time.perf_counter()
        out, _ = compress_terminal(it["args"].get("cmd") or it["id"], it["content"],
                                   session_id=it["id"], deadline=Deadline(200.0))
        ms.append((time.perf_counter() - t) * 1000)
        before += len(it["content"]); after += len(out)
    ms.sort()
    print(json.dumps({
        "python_lines_total": sum(loc.values()),
        "modules": len(MODS),
        "top_level_defs": total,
        "unreferenced_defs": dead,
        "unreferenced_detail": dead_rows,
        "dual_import_try_blocks": import_blocks,
        "terminal_corpus_reduction_pct": round(100 * (before - after) / before, 2),
        "terminal_ms_total": round(sum(ms), 1),
        "terminal_ms_p95": round(ms[int(len(ms) * 0.95) - 1], 1),
    }, indent=1))

if __name__ == "__main__":
    main()
