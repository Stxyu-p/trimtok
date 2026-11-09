"""JSON envelope folding.

Tool results routinely arrive wrapped in a JSON object:

    {"output": "<30k chars of terminal text>"}
    {"success": true, "items": [{...}, {...}]}

``minify_json`` only strips whitespace from these, so they sit near 4% savings
while the payload inside is a shape trimtok already compresses well. These
helpers unwrap the wrapper, fold the payload, and re-emit the envelope without
losing a byte: the original is in CCR and the wrapper keys are unchanged.

Every function is fail-open: any parse problem, unsupported shape, or failed
round-trip returns ``None`` and the caller keeps the original text.
"""

from __future__ import annotations

import json

MAX_DEPTH = 4
MIN_STRING_CHARS = 1024
MIN_ROWS = 3
MAX_FIELDS = 128


def _walk_strings(node, path=(), depth=0):
    """Yield (path, value) for every string >= MIN_STRING_CHARS, deepest last."""
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                continue
            yield from _walk_strings(value, path + (key,), depth + 1)
            if isinstance(value, str) and len(value) >= MIN_STRING_CHARS:
                yield path + (key,), value
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_strings(value, path + (index,), depth + 1)


def _walk_arrays(node, path=(), depth=0):
    """Yield (path, rows) for arrays of >= MIN_ROWS dicts."""
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                continue
            if (
                isinstance(value, list)
                and len(value) >= MIN_ROWS
                and value
                and all(isinstance(row, dict) for row in value)
            ):
                yield path + (key,), value
            yield from _walk_arrays(value, path + (key,), depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_arrays(value, path + (index,), depth + 1)


def _set_path(node, path, value):
    """Return a copy of ``node`` with ``path`` replaced by ``value``."""
    if not path:
        return value
    head, rest = path[0], path[1:]
    if isinstance(node, list):
        copy = list(node)
        copy[head] = _set_path(copy[head], rest, value)
        return copy
    copy = dict(node)
    copy[head] = _set_path(copy[head], rest, value)
    return copy


def _get_path(node, path):
    for key in path:
        node = node[key]
    return node


def _path_label(path) -> str:
    return ".".join(str(part) for part in path)


def fold_envelope_text(raw_text: str, compress_inner) -> tuple[str, dict] | None:
    """Compress the dominant string inside a JSON object and re-emit the envelope.

    ``compress_inner(text) -> str`` is supplied by the caller so this module
    stays free of compressor imports. The result is verified: the envelope
    parses, every non-target value is unchanged, and the target string hashes
    back to the original.
    """
    try:
        data = json.loads(raw_text)
    except Exception:
        return None
    if not isinstance(data, dict) or not data:
        return None
    candidates = list(_walk_strings(data))
    if not candidates:
        return None
    path, text = max(candidates, key=lambda item: len(item[1]))
    if len(text) < MIN_STRING_CHARS:
        return None
    try:
        folded = compress_inner(text)
    except Exception:
        return None
    if not isinstance(folded, str) or len(folded) >= len(text):
        return None
    if not folded.strip() or folded == text:
        return None
    try:
        candidate = json.dumps(
            _set_path(data, path, folded), ensure_ascii=False, separators=(",", ":")
        )
    except Exception:
        return None
    if len(candidate) >= len(raw_text):
        return None
    try:
        restored = json.loads(candidate)
    except Exception:
        return None
    if _get_path(restored, path) != folded:
        return None
    return candidate, {
        "envelope_path": _path_label(path),
        "envelope_inner_before": len(text),
        "envelope_inner_after": len(folded),
    }


def fold_envelope_array(raw_text: str) -> tuple[str, dict] | None:
    """Fold an array of records nested inside a JSON object into TOON buckets.

    Mixed-schema rows are bucketed by key set so a single odd row cannot veto
    the whole table. The envelope's scalar siblings are kept verbatim as JSON
    so nothing outside the target array moves.
    """
    try:
        from .toon_codec import encode, toon_decode
    except Exception:
        try:
            from toon_codec import encode, toon_decode
        except Exception:
            return None
    try:
        data = json.loads(raw_text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    found = list(_walk_arrays(data))
    if not found:
        return None
    path, rows = max(found, key=lambda item: len(item[1]))
    if len({frozenset(row.keys()) for row in rows}) > MAX_FIELDS:
        return None

    buckets: dict[tuple, list[tuple[int, dict]]] = {}
    for index, row in enumerate(rows):
        if "__tt_row__" in row:
            return None
        buckets.setdefault(tuple(sorted(row)), []).append((index, row))
    if not buckets:
        return None

    blocks: list[str] = []
    restored: list = [None] * len(rows)
    for bucket_id, indexed in enumerate(buckets.values(), 1):
        tagged = [{"__tt_row__": index, **row} for index, row in indexed]
        table = encode(tagged)
        if table is None:
            return None
        decoded = toon_decode(table)
        if not isinstance(decoded, list) or len(decoded) != len(tagged):
            return None
        for record in decoded:
            index = record.pop("__tt_row__", None)
            if not isinstance(index, int) or not 0 <= index < len(restored):
                return None
            if restored[index] is not None:
                return None
            restored[index] = record
        blocks.append(f"##bucket {bucket_id} rows={len(indexed)}\n{table}")
    if restored != rows:
        return None

    siblings = {key: value for key, value in data.items() if (key,) != path}
    header = f"@envelope:{json.dumps(siblings, ensure_ascii=False, separators=(',', ':'))}"
    label = f"@{_path_label(path)}[{len(rows)}]"
    candidate = "\n".join(
        [f"{header}", f"{label}", *blocks]
    )
    if len(candidate) >= len(raw_text):
        return None
    return candidate, {
        "envelope_path": _path_label(path),
        "envelope_rows": len(rows),
    }
