"""trimtok toon_codec: lossless JSON→TOON columnar transcoder (Trimtok 4.0).

Whole-document JSON arrays (or nested dict wrappers holding one) with a uniform
schema transcode to a compact columnar table:

    @prefix(~/)=C:\\work\\project\\src\\
    @data.web[3]{path,lines,status}:
    ~/auth.py|120|ok
    ~/db.py|350|ok
    ~/cache.py|88|ok

Zero loss of intelligence is the hard rail: every value survives, and the
encoder refuses (returns the input untouched) unless:

  * the shape is supported (>=3 uniform dict rows, <=24 fields),
  * a full decode() round-trip reproduces the parsed document EXACTLY
    (type-strict, key-order-insensitive in-memory compare),
  * the table is smaller than the input, and
  * the estimated BPE gain clears MIN_BPE_GAIN (Token-Gain Gate).

Never raises — refusals return the original text.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from .signals import estimate_bpe_gain, fast_json_loads
except ImportError:
    from signals import estimate_bpe_gain, fast_json_loads

MIN_ROWS = 3
MAX_FIELDS = 24
MIN_BPE_GAIN = 4
_MIN_DOC_CHARS = 64
_MAX_RAW_CELL = 160  # raw cells longer than this get quoted for safety
_MAX_INLINE_JSON_CELL = 120

_ALIAS = "~/"
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_HDR_RE = re.compile(r"^@([A-Za-z_][A-Za-z0-9_.]*)\[(\d+)\]\{(.*)\}:$")
_PREFIX_RE = re.compile(r"^@prefix\((.+?)\)=(.*)$")
_NUM_RE = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_BAD_RE = re.compile(r'["|\r\n]|^\s|\s$')
_BAD_FIELD_CHARS = ',{}[]"|\r\n'


def _encode_cell(v: Any, prefix: str, cache: dict | None = None) -> str | None:
    """Render one cell; returns None if the value cannot be safely encoded."""
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
            or (r[0] in "[{")
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
    if tv in (list, dict):
        s = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
        if len(s) <= _MAX_INLINE_JSON_CELL and "\n" not in s and "\r" not in s:
            return s
        return None
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
    if c0 in "[{":
        return fast_json_loads(t)
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


def _find_records(data: Any, max_depth: int = 3) -> tuple[list[dict] | None, str | None, dict]:
    """Locate table records, dot-notation target path, and sibling meta dict."""
    if isinstance(data, list):
        if len(data) >= MIN_ROWS and all(isinstance(x, dict) for x in data):
            return data, None, {}
        return None, None, {}
    if not isinstance(data, dict):
        return None, None, {}

    # Fast 1-level path (matches v1)
    for k, v in data.items():
        if isinstance(v, list) and len(v) >= MIN_ROWS and all(isinstance(x, dict) for x in v):
            if _NAME_RE.match(k):
                meta = {mk: mv for mk, mv in data.items() if mk != k}
                return v, k, meta

    # Multi-level search (e.g. data.web, results.items) up to max_depth
    def _search(d: dict, path: list[str], depth: int):
        if depth > max_depth:
            return None
        for k, v in d.items():
            if not isinstance(k, str) or "." in k or not _NAME_RE.match(k):
                continue
            cur_path = path + [k]
            if isinstance(v, list) and len(v) >= MIN_ROWS and all(isinstance(x, dict) for x in v):
                return v, ".".join(cur_path)
            if isinstance(v, dict):
                found = _search(v, cur_path, depth + 1)
                if found is not None:
                    return found
        return None

    found = _search(data, [], 1)
    if found is not None:
        recs, target_path = found

        def _extract_meta(d: dict, parts: list[str]) -> dict:
            out = {}
            for k, v in d.items():
                if k == parts[0]:
                    if len(parts) == 1:
                        continue
                    if isinstance(v, dict):
                        sub = _extract_meta(v, parts[1:])
                        if sub:
                            out[k] = sub
                else:
                    out[k] = v
            return out

        meta = _extract_meta(data, target_path.split("."))
        return recs, target_path, meta

    return None, None, {}


def _encode_doc(data: Any) -> str | None:
    """Build the TOON table text for a supported doc, or None."""
    records, target, meta = _find_records(data)
    if records is None:
        return None
    if target is not None and not _NAME_RE.match(target):
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
            encoded_val = _encode_cell(v, prefix, enc_cache)
            if encoded_val is None:
                return None  # unsupported cell structure
            if prefix and isinstance(v, str) and v.startswith(prefix):
                used_prefix = True
            cells.append(encoded_val)
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
                meta = fast_json_loads(line[len("@meta:"):])
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

    # Reconstruct nested target path if any (e.g. data.web)
    if meta:
        base: dict = dict(meta)
        parts = name.split(".")
        cur = base
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = records
        return base
    if name != "rows":
        parts = name.split(".")
        if len(parts) > 1:
            base = {}
            cur = base
            for p in parts[:-1]:
                cur[p] = {}
                cur = cur[p]
            cur[parts[-1]] = records
            return base
        return {name: records}
    return records


def _strict_eq(a: Any, b: Any) -> bool:
    """Type-strict, key-order-insensitive equality in memory without json.dumps."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if len(a) != len(b) or set(a.keys()) != set(b.keys()):
            return False
        return all(_strict_eq(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_strict_eq(x, y) for x, y in zip(a, b))
    return a == b


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
        data = fast_json_loads(s)
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


def encode(data: Any) -> str | None:
    """Public helper to encode Python structure to TOON table."""
    return _encode_doc(data)


def decode(text: str) -> tuple[Any, Exception | None]:
    """Public helper to decode TOON table; returns (data, error)."""
    try:
        return toon_decode(text), None
    except Exception as e:
        return None, e


def strict_eq(a: Any, b: Any) -> bool:
    """Public helper for type-strict, key-order-insensitive comparison."""
    return _strict_eq(a, b)
