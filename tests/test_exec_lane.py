"""trimtok_exec: compute over a #ref without materialising it in context."""
import json
import os
import tempfile

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-execlane-"))

from trimtok import ccr
from trimtok.exec_lane import run

ROWS = [{"shape": s, "n": i} for i, s in enumerate(["json"] * 30 + ["mixed"] * 12)]


def test_exec_answers_aggregate_without_returning_the_payload():
    ref = ccr.put(json.dumps(ROWS))
    res = run(ref, "import json,os,collections;"
                   "a=json.load(open(os.environ['TRIMTOK_PAYLOAD']));"
                   "print(sorted(collections.Counter(x['shape'] for x in a).items()))")
    assert res["ok"] is True
    assert res["output"] == "[('json', 30), ('mixed', 12)]"
    assert len(res["output"]) < 60          # 1.5KB payload, 30-byte answer
    assert res["payload_bytes"] > 1000


def test_exec_reports_missing_ref_and_errors_instead_of_raising():
    assert run("#nope", "print(1)")["ok"] is False
    ref = ccr.put("hello")
    bad = run(ref, "raise SystemExit(3)")
    assert bad["ok"] is False and bad["exit_code"] == 3
    assert run(ref, "")["ok"] is False


def test_exec_enforces_the_timeout():
    ref = ccr.put("x")
    res = run(ref, "import time; time.sleep(5)", timeout=0.5)
    assert res["ok"] is False and res["exit_code"] == 124
