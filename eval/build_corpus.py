"""Materialize a REAL payload corpus for trimtok eval. No synthetic text, no mocks.

Every item records its provenance (`origin`): the file path it was read from or
the exact shell command that produced it. Rebuild with: python eval/build_corpus.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "corpus"
HOME = Path.home()
items: list[dict] = []


def add(id_: str, kind: str, tool: str, content: str, *, expect: list[str], origin: str, args=None):
    if not content or len(content) < 200:
        print(f"  SKIP {id_} (too small: {len(content)})")
        return
    items.append({
        "id": id_,
        "kind": kind,
        "tool": tool,
        "args": args or {},
        "expect": expect,
        "origin": origin,
        "chars": len(content),
        "content": content,
    })


def numbered(path_or_text: "str | Path", *, from_file=True, start=1) -> str:
    text = Path(path_or_text).read_text(encoding="utf-8", errors="replace") if from_file else str(path_or_text)
    lines = text.splitlines()
    return "\n".join(f"{start + i}|{l}" for i, l in enumerate(lines))


def sh(cmd: str, cwd: Path | None = None) -> str:
    p = subprocess.run(["bash", "-lc", cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, errors="replace", timeout=120)
    return p.stdout + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- read_file band
SRC = ROOT / "trimtok"
for f in ["dedup.py", "core.py", "signals.py", "toon_codec.py", "__init__.py", "ccr.py", "pruners.py",
          "delta_engine.py", "gate.py", "store.py"]:
    p = SRC / f
    if p.exists():
        n = len(read(p).splitlines())
        add(f"rf-{f}", "read_file", "read_file", numbered(p),
            expect=["symbols"], origin=str(p), args={"path": str(p), "offset": 1, "limit": 2000})

add("rf-readme", "read_file", "read_file", numbered(ROOT / "README.md"),
    expect=["verbatim"], origin=str(ROOT / "README.md"),
    args={"path": str(ROOT / "README.md"), "offset": 1, "limit": 2000})
add("rf-changelog", "read_file", "read_file", numbered(ROOT / "CHANGELOG.md"),
    expect=["verbatim"], origin=str(ROOT / "CHANGELOG.md"),
    args={"path": str(ROOT / "CHANGELOG.md"), "offset": 1, "limit": 2000})

SK = Path("C:/Users/gugmn/AppData/Local/hermes/skills")
for sk in ["software-development/context-compression-maintenance/SKILL.md",
           "software-development/systematic-debugging/SKILL.md",
           "research/grounded-citations/SKILL.md"]:
    p = SK / sk
    if p.exists():
        n = len(read(p).splitlines())
        add(f"rf-skill-{p.parent.name}", "read_file", "read_file", numbered(p),
            expect=["verbatim"], origin=str(p), args={"path": str(p), "offset": 1, "limit": 2000})

# targeted slice (offset > 1) -- exercises the slice lane
if (SRC / "dedup.py").exists():
    _dl = read(SRC / "dedup.py").splitlines()[899:1099]
    add("rf-slice-dedup", "read_file", "read_file", numbered("\n".join(_dl), from_file=False, start=900),
        expect=["symbols"], origin=f"{SRC / 'dedup.py'} lines 900-1100",
        args={"path": str(SRC / "dedup.py"), "offset": 900, "limit": 200})

# ---------------------------------------------------------------- json / data band
for jp, jid in [(Path("C:/Users/gugmn/AppData/Roaming/npm/node_modules/9router/app/package.json"), "9router-pkg"),
                (HOME / ".9router/model-catalog.json", "model-catalog"),
                (Path("C:/Users/gugmn/AppData/Local/hermes/plugins/trimtok/plugin.yaml"), "plugin-yaml")]:
    if jp.exists():
        add(f"tool-{jid}", "tool", "read_file", numbered(jp),
            expect=["verbatim"], origin=str(jp), args={"path": str(jp), "offset": 1, "limit": 2000})

ul = HOME / ".trimtok/usage.jsonl"
if ul.exists():
    lines = ul.read_text(encoding="utf-8", errors="replace").splitlines()
    add("tool-usage-jsonl", "tool", "read_file", numbered("\n".join(lines[:400]), from_file=False),
        expect=["verbatim"], origin=f"{ul} lines 1-400", args={"path": str(ul), "offset": 1, "limit": 2000})

# ---------------------------------------------------------------- terminal band
TMP = Path(tempfile.mkdtemp(prefix="ttc-"))
repo = TMP / "r"
repo.mkdir()
(repo / "a.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
(repo / "b.py").write_text("def c():\n    return 3\n")
for cmd in ["git init -q", "git add -A", "git -c user.email=a@b -c user.name=a commit -qm one"]:
    sh(cmd, repo)
(repo / "a.py").write_text("def a():\n    return 10\n\n\ndef b():\n    return 2\n\n\ndef d():\n    return 4\n")
(repo / "c.py").write_text("import os\n")

tc: list[tuple[str, str, list[str], Path | None]] = [
    ("t-git-status", "git status", ["faults"], repo),
    ("t-git-diff", "git diff", ["faults"], repo),
    ("t-git-log", "git log --stat -3", ["faults"], repo),
    ("t-pytest-core", "python tests/test_core.py", ["faults"], ROOT),
    ("t-pytest-torture", "python tests/test_torture.py", ["faults"], ROOT),
    ("t-pip-list", "python -m pip list", ["faults"], None),
    ("t-ls-tr", "ls -R trimtok tests", ["faults"], ROOT),
    ("t-grep-def", "grep -rn 'def ' trimtok", ["faults"], ROOT),
    ("t-find-py", "find . -name '*.py'", ["faults"], ROOT),
    ("t-tb-exception", "python -c 'raise RuntimeError(\"boom\")'", ["faults"], None),
    ("t-tb-import", "python -c 'import sys; sys.path.insert(0,\"/nope\"); import trimtok_x' ", ["faults"], None),
    ("t-adb", "adb devices", ["faults"], None),
    ("t-uname", "uname -a", ["faults"], None),
    ("t-node-eval", "node -e 'let a=[];for(let i=0;i<50;i++)a.push({i,ok:i%7});console.log(JSON.stringify(a))'", ["faults"], None),
    ("t-npm-ls", "npm ls --depth=0", ["faults"], Path("C:/Users/gugmn/AppData/Roaming/npm/node_modules/9router/app")),
    ("t-ccr-dump", "python -c '\nimport sqlite3,pathlib\np=pathlib.Path.home()/\".trimtok/trimtok_ccr_v2.sqlite3\"\nc=sqlite3.connect(p)\nfor r in c.execute(\"select name from sqlite_master where type=%s\"%chr(39)+\"table\"+chr(39)):print(r)\nfor r in c.execute(\"select key,length(val) from blobs limit 40\"):print(r)\n'", ["faults"], None),
]
for cid, cmd, exp, cwd in tc:
    out = sh(cmd, cwd)
    add(cid, "terminal", "terminal", out, expect=exp, origin=f"$ {cmd}" + (f"  (cwd={cwd})" if cwd else ""))

# ---------------------------------------------------------------- tool band
add("tool-skillview", "tool", "skill_view", read(SK / "software-development/context-compression-maintenance/SKILL.md"),
    expect=["verbatim"], origin=f"skill_view({SK / 'software-development/context-compression-maintenance/SKILL.md'})")
add("tool-skillslist", "tool", "skills_list",
    "\n".join(f"- {p.parent.name}: {p.read_text(encoding='utf-8', errors='replace')[:120].splitlines()[0]}"
              for p in SK.rglob("SKILL.md")),
    expect=["verbatim"], origin=f"notes from {SK}/**/SKILL.md")

# ---------------------------------------------------------------- real logs (fault-dense)
LOGS = Path("C:/Users/gugmn/AppData/Local/hermes/logs")
for name, n in [("errors.log", 1200), ("agent.log", 2000), ("gateway-stdio.log", 1200)]:
    if (LOGS / name).exists():
        add(f"t-log-{name.split('.')[0]}", "terminal", "terminal", sh(f"tail -n {n} '{LOGS / name}' | sed 's/\\r$//'"),
            expect=["faults"], origin=f"$ tail -n {n} {LOGS / name}")

LOG = Path("C:/Users/gugmn/AppData/Local/hermes/logs/gateway.log")

OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.json"):
    old.unlink()
for it in items:
    (OUT / f"{it['id']}.json").write_text(json.dumps(it, ensure_ascii=False), encoding="utf-8")
total = sum(i["chars"] for i in items)
print(f"corpus: {len(items)} payloads, {total:,} chars ({total/1e6:.2f} MB) -> {OUT}")
by = {}
for i in items:
    by[i["kind"]] = by.get(i["kind"], 0) + 1
print("by kind:", by)
