"""A single huge line is data, not a log line: never run the fault regexes on it."""
import os
import tempfile

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="tt-largeline-"))

import trimtok.dedup as dedup


def test_huge_single_line_skips_fault_scan(monkeypatch):
    calls = []
    monkeypatch.setattr(dedup, "is_important", lambda line: calls.append(len(line)) or False)

    blob = "error " + ("x" * 500_000)
    out, saved = dedup._fold_intra_templates(blob)

    assert out == blob
    assert saved == 0
    assert calls == []          # the 500KB line never reached the regex scan


def test_normal_repeated_lines_still_fold():
    line = "2026-11-06 12:00:00 request id 12345 handled ok"
    text = "\n".join(line.replace("12345", str(i)) for i in range(10))
    out, saved = dedup._fold_intra_templates(text)
    assert saved > 0
    assert len(out) < len(text)


def test_structured_log_lines_fold():
    # `status=200`/`user=42` used to veto the whole line (the old `=\s*\d+` guard),
    # which is why this lane never fired on real telemetry.
    line = "2026-11-06 12:00:00 GET /api/v1/items status=200 user=42 dur=12ms"
    text = "\n".join(line.replace("12ms", f"{i}ms") for i in range(10))
    out, saved = dedup._fold_intra_templates(text)
    assert saved > 0
    assert len(out) < len(text)


def test_bare_config_values_never_share_a_key():
    # The guard the veto exists for: distinct ports must not collapse into one key.
    text = "\n".join(f"PORT={port}" for port in (8080, 9090, 7070, 6060))
    out, saved = dedup._fold_intra_templates(text)
    assert saved == 0
    assert out == text
