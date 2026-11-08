"""pointer_large: a giant single-line blob ships as a #ref stub, nothing else does."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trimtok import ccr
from trimtok.core import compress_terminal


def _blob(n=50_000):
    return '{"name":"x","content":"' + "A" * n + '"}'


def test_giant_single_line_blob_becomes_pointer():
    blob = _blob()
    out, stats = compress_terminal("gh api repos/x/contents/y", blob, session_id="pl-1")
    assert stats["gate"] == "ok"
    assert stats["kind"] == "pointer_large"
    assert len(out) < 1000, len(out)
    assert "trimtok_retrieve" in out
    assert ccr.get(stats["digest"]) == blob


def test_multiline_payload_is_not_a_pointer():
    prose = "\n".join(f"## heading {i} with a sentence of prose to pad it" for i in range(400))
    out, stats = compress_terminal("skill_view", prose, session_id="pl-2")
    assert stats["kind"] != "pointer_large"


def test_blob_below_threshold_is_untouched():
    blob = _blob(6000)
    out, stats = compress_terminal("gh api repos/x/contents/y", blob, session_id="pl-3")
    assert stats["kind"] != "pointer_large"
    assert len(out) == len(blob)
