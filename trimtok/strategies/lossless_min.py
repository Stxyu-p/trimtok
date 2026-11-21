"""Lossless cosmetic minification strategy (Baseline Strategy B).

Cleans license headers, copyright blocks, ASCII divider lines, and redundant blank
lines without modifying or folding a single line of executable logic.
"""
from __future__ import annotations

import re
from typing import Any

_LINE_PREFIX_RE = re.compile(r"^(\s*\d+\|)(.*)$")
_ASCII_BANNER_RE = re.compile(r"^\s*(?:#|//|/\*|\*)\s*[-=_*#~]{5,}\s*(?:\*/)?$")
_LICENSE_TRIGGER_RE = re.compile(
    r"(?i)(?:\bmit license\b|\bapache license\b|\bbsd-style\b|\bcopyright(?:\s+\(c\))?|\ball rights reserved\b|\bgnu general public\b|\bmozilla public\b)"
)
_FN_SIG_RE = re.compile(r"^\s*(?:async\s+)?(?:def|func|function)\s+([a-zA-Z0-9_]+)")


def minify_lossless(text: str, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Clean decorative and boilerplate noise while keeping 100% logic."""
    meta = meta or {}
    lang = (meta.get("lang") or "").lower()
    domain = (meta.get("domain") or "").lower()
    is_prose = lang in ("markdown", "text", "html") or domain in ("web-doc", "spec-doc")

    lines = text.split("\n")
    out_lines: list[str] = []

    in_license_block = False
    in_block_comment = False
    block_buf: list[str] = []
    block_is_license = False
    kept_fn_count = 0
    prev_was_blank = False

    for line in lines:
        m = _LINE_PREFIX_RE.match(line)
        prefix = m.group(1) if m else ""
        content = m.group(2) if m else line
        stripped = content.strip()

        if _FN_SIG_RE.match(stripped):
            kept_fn_count += 1

        if not is_prose:
            # Multi-line /* ... */ blocks: buffer and ONLY drop when confirmed as license/copyright
            if in_block_comment:
                if "*/" in stripped:
                    in_block_comment = False
                    if _LICENSE_TRIGGER_RE.search(stripped):
                        block_is_license = True
                    tail_code = stripped.split("*/", 1)[1].strip()
                    if block_is_license:
                        if tail_code:
                            out_lines.append(f"{prefix}{tail_code}")
                    else:
                        out_lines.extend(block_buf)
                        out_lines.append(line)
                    block_buf = []
                    block_is_license = False
                    in_license_block = False
                    continue
                if stripped.startswith(("*", "//", "/*", "#", ";")) or not stripped:
                    if _LICENSE_TRIGGER_RE.search(stripped):
                        block_is_license = True
                    block_buf.append(line)
                    continue
                # Unclosed opener (e.g. inside string literal): flush buffered lines verbatim
                out_lines.extend(block_buf)
                block_buf = []
                in_block_comment = False
                block_is_license = False
                in_license_block = False
            elif stripped.startswith("/*"):
                if "*/" not in stripped:
                    in_block_comment = True
                    block_is_license = bool(_LICENSE_TRIGGER_RE.search(stripped))
                    block_buf = [line]
                    continue
                # Inline /* ... */ comment: only drop if license/banner and no executable code follows
                tail_code = stripped.split("*/", 1)[1].strip()
                if not tail_code and (_LICENSE_TRIGGER_RE.search(stripped) or _ASCII_BANNER_RE.match(stripped)):
                    continue

            # Single-line license / ASCII banner comments (skipped for markdown/prose so headings & bullets stay intact)
            if stripped.startswith(("//", "#", "*")):
                if _LICENSE_TRIGGER_RE.search(stripped):
                    in_license_block = True
                    continue
                if in_license_block and (
                    "license" in stripped.lower()
                    or "copyright" in stripped.lower()
                    or "http" in stripped.lower()
                ):
                    continue
                in_license_block = False
                if _ASCII_BANNER_RE.match(stripped):
                    continue
            else:
                in_license_block = False

        if not stripped:
            if prev_was_blank:
                continue
            prev_was_blank = True
            out_lines.append(f"{prefix}")
            continue
        prev_was_blank = False

        out_lines.append(line)

    if block_buf:
        out_lines.extend(block_buf)

    res = "\n".join(out_lines)
    before_len = len(text)
    after_len = len(res)
    return res, {
        "gate": "ok",
        "before_chars": before_len,
        "after_chars": after_len,
        "saved_chars": max(0, before_len - after_len),
        "kept_functions": kept_fn_count,
        "folded_functions": 0,
    }
