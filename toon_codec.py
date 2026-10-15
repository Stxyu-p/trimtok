"""trimtok toon_codec: lossless JSON→TOON columnar transcoder (Trimtok 2.0 Phase 3).

Whole-document JSON arrays (or dict wrappers holding one) with a uniform,
flat schema transcode to a compact columnar table:

    @prefix(~/)=C:\\work\\project\\src\\
    @rows[3]{path,lines,status}:
    ~/auth.py|120|ok
    ~/db.py|350|ok
    ~/cache.py|88|ok

Zero loss of intelligence is the hard rail: every value survives, and the
encoder refuses (returns the input untouched) unless

  * the shape is supported (>=3 uniform flat dict rows, <=24 fields),
  * a full decode() round-trip reproduces the parsed document EXACTLY
    (type-strict, key-order-insensitive compare),
  * the table is smaller than the input, and
  * the estimated BPE gain clears MIN_BPE_GAIN (Token-Gain Gate — a fold
    must be worth its tokens, not just its bytes).

Deliberately conservative v1 (per plan kill-criteria "flat / 1-level only"):
nested values, non-uniform key sets, or exotic wrapper names are left to the
other folds. Extend only together with the same round-trip gate.
Never raises — refusals return the original text.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from .bpe_estimator import estimate_bpe_gain
except ImportError:  # direct unit-test import
    from bpe_estimator import estimate_bpe_gain

MIN_ROWS = 3
MAX_FIELDS = 24
MIN_BPE_GAIN = 4
_MIN_DOC_CHARS = 64
_MAX_RAW_CELL = 160  # raw cells longer than this get quoted for safety

_ALIAS = "~/"
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HDR_RE = re.compile(r"^@([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]\{(.*)\}:$")
_PREFIX_RE = re.compile(r"^@prefix\((.+?)\)=(.*)$")
_NUM_RE = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_BAD_RE = re.compile(r'["|\r\n]|^\s|\s$')
_BAD_FIELD_CHARS = ',{}[]"|\r\n'


def _encode_cell(v: Any, prefix: str, cache: dict | None = None) -> str:
    """Render one scalar cell; strings quote whenever the raw form is ambiguous.

    `cache` (optional, per-doc) memoizes string renderings — tool payloads
    repeat the same values (statuses, flags) constantly.
    """
    if v is None:
        return "null"
    tv = type(v)
    if tv is str:
        if cache is not None:
            hit = cache.get(v)
            if hit is not None:
                return hit
        r = v
        substituted = False
        if prefix and v.startswith(prefix):
            r = _ALIAS + v[len(prefix):]
            substituted = True
        if (
            not r
            or len(r) > _MAX_RAW_CELL
            or _BAD_RE.search(r)
            or (len(r) in (4, 5) and r in ("null", "true", "false"))
            or (r[0] in "-.0123456789" and _NUM_RE.match(r))
            or (not substituted and r[0] == "~")
        ):
            out = json.dumps(v, ensure_ascii=False)
        else:
            out = r
        if cache is not None:
            cache[v] = out
        return out
    if tv is bool:
        return "true" if v else "false"
    if tv is int:
        return str(v)
    if tv is float:
        return repr(v)
    return json.dumps(v, ensure_ascii=False)


def _decode_cell(t: str, prefix: str | None) -> Any:
    """Inverse of _encode_cell for one cell token. Raises on malformed input."""
    if t == "":
        raise ValueError("empty cell")
    c0 = t[0]
    if c0 == '"':
        v = json.loads(t)
        if not isinstance(v, str):
            raise ValueError("quoted cell must decode to str")
        return v
    if c0 == "n" and t == "null":
        return None
    if c0 == "t" and t == "true":
        return True
    if c0 == "f" and t == "false":
        return False
    if c0 in "-.0123456789":
        if t.isdigit() or (c0 == "-" and t[1:].isdigit()):
            return int(t)
        if _NUM_RE.match(t):
            return float(t)
        return t
    if prefix is not None and c0 == "~" and t.startswith(_ALIAS):
        return prefix + t[len(_ALIAS):]
    return t


def _split_cells(line: str) -> list[str]:
    """Split a row on '|' outside quoted cells (backslash escapes honored)."""
    cells: list[str] = []
    buf: list[str] = []
    in_q = False
    esc = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_q:
            buf.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_q = False
            i += 1
            continue
        if c == '"':
            in_q = True
            buf.append(c)
            i += 1
            continue
        if c == "|":
            cells.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if in_q:
        raise ValueError("unterminated quoted cell")
    cells.append("".join(buf))
    return cells


def _common_prefix(records: list[dict]) -> str:
    """Longest common path prefix across qualifying strings (>= half the rows)."""
    vals = [
        v
        for r in records
        for v in r.values()
        if isinstance(v, str) and len(v) > 16 and ("/" in v or "\\" in v)
    ]
    if not vals or len(vals) < max(1, len(records) // 2):
        return ""
    cand = os.path.commonprefix(vals)
    sep = max(cand.rfind("/"), cand.rfind("\\"))
    if sep >= 10:
        return cand[: sep + 1]
    return ""


def _encode_doc(data: Any) -> str | None:
    """Build the TOON table text for a supported doc, or None."""
    records: list[dict] | None = None
    target: str | None = None
    meta: dict = {}
    if isinstance(data, list):
        if len(data) >= MIN_ROWS and all(isinstance(x, dict) for x in data):
            records = data
    elif isinstance(data, dict):
        for k, v in data.items():
            if (
                records is None
                and isinstance(v, list)
                and len(v) >= MIN_ROWS
                and all(isinstance(x, dict) for x in v)
            ):
                records = v
                target = k
            else:
                meta[k] = v
        if target is not None and not _NAME_RE.match(target):
            return None
    if records is None:
        return None
    fields = list(dict.fromkeys(k for r in records for k in r.keys()))
    if not fields or len(fields) > MAX_FIELDS:
        return None
    fset = set(fields)
    if any(set(r.keys()) != fset for r in records):
        return None
    if any(not k or any(c in k for c in _BAD_FIELD_CHARS) for k in fields):
        return None
    prefix = _common_prefix(records)
    used_prefix = False
    row_lines: list[str] = []
    enc_cache: dict = {}
    for r in records:
        cells = []
        for k in fields:
            v = r.get(k)
            if v is not None and not isinstance(v, (str, int, float, bool)):
                return None  # nested / unsupported value: flat-only v1
            if prefix and isinstance(v, str) and v.startswith(prefix):
                used_prefix = True
            cells.append(_encode_cell(v, prefix, enc_cache))
        row_lines.append("|".join(cells))
    name = target or "rows"
    out: list[str] = []
    if used_prefix:
        out.append(f"@prefix({_ALIAS})={prefix}")
    if meta:
        out.append("@meta:" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    out.append(f"@{name}[{len(records)}]{{{','.join(fields)}}}:")
    out.extend(row_lines)
    return "\n".join(out)


def toon_decode(text: str) -> Any:
    """Parse a TOON table back to Python data. Raises on malformed text."""
    prefix: str | None = None
    meta: dict | None = None
    name: str | None = None
    count = 0
    fields: list[str] | None = None
    records: list[dict] = []
    dcache: dict = {}
    miss = object()
    for line in text.split("\n"):
        if not line:
            continue
        if line[0] == "@":
            if line.startswith("@prefix("):
                m = _PREFIX_RE.match(line)
                if not m:
                    raise ValueError("bad prefix line")
                prefix = m.group(2)
                continue
            if line.startswith("@meta:"):
                meta = json.loads(line[len("@meta:"):])
                if not isinstance(meta, dict):
                    raise ValueError("meta must be an object")
                continue
            m = _HDR_RE.match(line)
            if m and fields is None:
                name = m.group(1)
                count = int(m.group(2))
                fields = m.group(3).split(",")
                if any(not f for f in fields):
                    raise ValueError("bad fields")
                continue
        if fields is None:
            raise ValueError("row before header")
        # Fast path: a plain split is exact whenever every cell is quote-free
        # (cells containing "|" are always quoted by the encoder, so an
        # over-count can only come from a quoted cell — fall back then).
        cells = line.split("|")
        if len(cells) != len(fields):
            cells = _split_cells(line)
        if len(cells) != len(fields):
            raise ValueError("cell count mismatch")
        rec = {}
        for f, c in zip(fields, cells):
            got = dcache.get(c, miss)
            if got is miss:
                got = _decode_cell(c, prefix)
                dcache[c] = got
            rec[f] = got
        records.append(rec)
    if fields is None:
        raise ValueError("no header")
    if len(records) != count:
        raise ValueError("row count mismatch")
    if meta:
        base: dict = dict(meta)
        base[name] = records
        return base
    if name != "rows":
        raise ValueError("unexpected table name without meta")
    return records


def _strict_eq(a: Any, b: Any) -> bool:
    """Type-strict, key-order-insensitive equality via canonical JSON dumps."""
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(
        b, ensure_ascii=False, sort_keys=True
    )


def fold_json_to_toon(raw_text: str) -> str:
    """Lossless JSON→TOON fold for a whole-document JSON string.

    Returns the ORIGINAL text whenever the doc is unsupported, the decode
    round-trip is not exact, the table is not smaller, or the BPE gain is
    below MIN_BPE_GAIN. Never raises.
    """
    try:
        if not isinstance(raw_text, str) or len(raw_text) < _MIN_DOC_CHARS:
            return raw_text
        s = raw_text.strip()
        if not (s.startswith(("{", "[")) and s.endswith(("}", "]"))):
            return raw_text
        data = json.loads(s)
        toon = _encode_doc(data)
        if toon is None or len(toon) >= len(s):
            return raw_text
        if estimate_bpe_gain(s, toon) < MIN_BPE_GAIN:
            return raw_text
        if not _strict_eq(toon_decode(toon), data):
            return raw_text
        return toon
    except Exception:
        return raw_text
