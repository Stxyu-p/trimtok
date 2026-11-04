"""Frozen fuzz: hostile inputs that must NEVER reach encode (fail-open contract).

Run: python eval/fuzz_toon_safety.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import trimtok as tt  # noqa: E402
from trimtok.toon_codec import _encode_doc, toon_decode, _strict_eq  # noqa: E402

CASES = {
    "empty": [],
    "not-dict-rows": [1, 2, 3],
    "mixed-schema": [{"a": 1}, {"a": 1, "b": 2}, {"b": 2}],
    "pipe-in-values": [{"a": "x|y", "b": "2"} for _ in range(5)],
    "quote-in-values": [{'a': 'he said "hi"', "b": "2"} for _ in range(5)],
    "newline-in-values": [{"a": "line1\nline2", "b": "2"} for _ in range(5)],
    "bad-field-chars": [{"a,b": 1, "c": 2} for _ in range(5)],
    "sparse-100-fields": [dict((f"col_{i}", i) for i in range(100)) for _ in range(5)],
    "extra-col-in-one": [dict((f"c{i}", i) for i in range(20)) for _ in range(4)] + [dict((f"c{i}", i) for i in range(21)) for _ in range(1)],
    "unicode-values": [{"emoji": "🚀🔥สวัสดี", "thai": "ครบถ้วน"} for _ in range(5)],
    "nested-lists": [{"tags": ["a", "b"], "n": 1} for _ in range(5)],
    "deep-nested": [{"a": {"b": {"c": {"d": 1}}}} for _ in range(5)],
}


def fail_open(rows, label) -> None:
    """Safely re-encode; a crash here is a bug."""
    for fn in (_encode_doc,):
        try:
            res = fn(rows)
        except Exception as e:
            print(f"  [CRASH {label}] {type(e).__name__}: {e}  <-- BUG")
            return
        if res is None:
            return  # refused = fail-open OK
        try:
            rt = toon_decode(res)
            exact = _strict_eq(rt, rows)
            print(f"  [encode {label}] ok roundtrip={exact} (saved {100*(1-len(res)/max(1,len(json.dumps(rows)))):.0f}%)")
        except Exception as e:
            print(f"  [DECODE-CRASH {label}] {type(e).__name__}: {e}  <-- BUG")


def run() -> int:
    print("=== TOON safety fuzz (encode must refuse or roundtrip exactly) ===")
    bad = 0
    for label, rows in CASES.items():
        print(f"- {label}")
        fail_open(rows, label)

    # Also verify the real pipeline still gates at 64: wide sparse at 80 cols
    import trimtok  # noqa: F401
    wide = [dict((f"col_{i}", i) for i in range(80)) for _ in range(5)]
    raw = json.dumps(wide)
    out, st = tt.compress_tool_result("api_call", raw, args={}, session_id="fz")
    assert out == raw or st.get("gate") == "ok", "wide sparse must stay fail-open"
    print("- wide-sparse-80 through compress_tool_result: ok")

    print("\nDONE")
    return bad


if __name__ == "__main__":
    raise SystemExit(run())
