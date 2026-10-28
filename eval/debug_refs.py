"""Diagnose C1: which refs fail to resolve, and what the store holds. Prints <= 2KB."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TRIMTOK_DATA_DIR", str(Path(tempfile.mkdtemp(prefix="tt-dbg-"))))

import trimtok as tt
from trimtok.ccr import get as ccr_get
from trimtok.core import compress_terminal
from trimtok.signals import Deadline

REF_RE = re.compile(r"#([0-9A-Za-z]{1,10})|retrieve:([0-9a-f]{8,64})")
corpus = {json.loads(p.read_text(encoding="utf-8"))["id"]: json.loads(p.read_text(encoding="utf-8"))
          for p in sorted((Path(__file__).parent / "corpus").glob("*.json"))}

for pid in sys.argv[1:]:
    it = corpus[pid]
    d = Deadline(200.0)
    if it["kind"] == "terminal":
        out, st = compress_terminal(pid, it["content"], session_id=pid, deadline=d)
    else:
        out, st = tt.compress_tool_result(it["tool"], it["content"], args=it["args"] or None, session_id=pid, deadline=d)
    out = out if isinstance(out, str) else it["content"]
    refs = set()
    for m in REF_RE.finditer(out):
        refs.add("#" + m.group(1) if m.group(1) else m.group(2))
    print(f"\n== {pid} gate={st.get('gate')} kind={st.get('kind')} before={len(it['content'])} after={len(out)} refs={len(refs)}")
    print("   head:", repr(out[:160]))
    for r in list(refs)[:6]:
        v = ccr_get(r, dir=Path(os.environ["TRIMTOK_DATA_DIR"]))
        print(f"   ref {r!r} -> {'MISS' if v is None else f'{len(v)} chars'}")
