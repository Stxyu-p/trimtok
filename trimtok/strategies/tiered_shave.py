"""Tiered Semantic Shaving with Line Anchors (Strategy C - Proposed).

Balances high token reduction with single-turn comprehension and 100% patch safety:
1. Cleans license headers, copyright noise, and ASCII banner lines.
2. Web / Docs: Strips cookie banners, breadcrumbs, social share links.
3. Small Code (< 12KB): Keeps 100% of executable statements intact.
4. Large Code (> 20KB): Preserves short utility functions (< 20 lines) in full,
   folding only oversized method bodies into line-anchored stubs.
"""
from __future__ import annotations

import re
from typing import Any

from .lossless_min import minify_lossless
from ..pruners import fold_text_structure

_LINE_PREFIX_RE = re.compile(r"^(\s*(\d+)\|)(.*)$")
_PY_FN_RE = re.compile(r"^\s*(?:async\s+)?def\s+([a-zA-Z0-9_]+)\s*\(")
_JS_FN_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_]+)\s*\(")
_GO_FN_RE = re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)\s*\(")

_WEB_NOISE_RE = re.compile(
    r"(?i)(?:cookie\s+(?:notice|policy|settings)|accept\s+all|reject\s+non-essential|"
    r"navigation:\s+home\s*>|share\s+on\s+(?:twitter|x|facebook|linkedin)|"
    r"subscribe\s+to\s+our\s+newsletter|advertisement:)"
)
_LINK_DEBLOAT_RE = re.compile(
    r"\[([^\]\n]{1,120})\]\((?:https?://|/)[^)\"\s]+(?:\s+['\"][^'\"]*['\"])?\)"
)

def _is_web_or_doc(domain: str, lang: str) -> bool:
    return domain in ("web-doc", "spec-doc") or lang in ("markdown", "text", "html")

def shave_tiered(text: str, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Apply tiered semantic shaving with single-turn comprehension guarantee."""
    meta = meta or {}
    domain = meta.get("domain", "")
    lang = (meta.get("lang") or "").lower()

    # Step 1: Lossless cosmetic cleanup (licenses, ascii banners, blank runs)
    clean_text, min_stats = minify_lossless(text, meta)
    
    # If small code, lossless minification is the whole tier (100% logic retained)
    if domain == "code-small" or (domain != "code-large" and not _is_web_or_doc(domain, lang) and len(text) < 14_000):
        return clean_text, min_stats

    lines = clean_text.split("\n")
    out_lines: list[str] = []
    kept_fn = 0
    folded_fn = 0

    # Case A: Web or Document
    if _is_web_or_doc(domain, lang):
        # 1. Strip web boilerplate lines
        filtered_lines = []
        for line in lines:
            m = _LINE_PREFIX_RE.match(line)
            content = m.group(3) if m else line
            if _WEB_NOISE_RE.search(content):
                continue
            filtered_lines.append(line)
        interim_text = "\n".join(filtered_lines)
        # Strip URL targets from inline markdown links (keeps anchor text)
        interim_text = _LINK_DEBLOAT_RE.sub(r"\1", interim_text)

        # 2. For markdown / docs >= 2000 chars, fold long prose runs
        if len(interim_text) >= 2000:
            from ..dedup import _unwrap_numbered_lines
            raw_body, prefixes, line_nos, _, _ = _unwrap_numbered_lines(interim_text)
            folded_doc, d_saved = fold_text_structure(raw_body, ref="#doc", line_prefixes=prefixes, line_nos=line_nos)
            if d_saved > 200:
                interim_text = folded_doc

        before_len = len(text)
        after_len = len(interim_text)
        return interim_text, {
            "gate": "ok",
            "before_chars": before_len,
            "after_chars": after_len,
            "saved_chars": max(0, before_len - after_len),
            "kept_functions": 0,
            "folded_functions": 0,
        }

    # Case B: Large Code (> 14KB)
    # Pre-parse function blocks to measure their length
    is_brace_lang = lang in ("javascript", "typescript", "go", "c", "cpp", "rust", "java")
    
    # Collect function boundaries
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _LINE_PREFIX_RE.match(line)
        prefix = m.group(1) if m else ""
        content = m.group(3) if m else line
        stripped = content.lstrip()
        indent = len(content) - len(stripped)

        fn_match = False
        if is_brace_lang:
            fn_match = bool(_JS_FN_RE.match(stripped) or _GO_FN_RE.match(stripped))
        else:
            fn_match = bool(_PY_FN_RE.match(stripped))

        if fn_match:
            # Measure function span
            fn_start = i
            fn_end = i
            if is_brace_lang:
                brace_cnt = content.count("{") - content.count("}")
                j = i + 1
                while j < len(lines) and brace_cnt > 0:
                    c = lines[j].split("|", 1)[-1] if "|" in lines[j] else lines[j]
                    brace_cnt += c.count("{") - c.count("}")
                    j += 1
                fn_end = j - 1
            else:
                j = i + 1
                while j < len(lines):
                    next_content = lines[j].split("|", 1)[-1] if "|" in lines[j] else lines[j]
                    next_strip = next_content.strip()
                    next_indent = len(next_content) - len(next_content.lstrip())
                    if next_strip and next_indent <= indent:
                        break
                    j += 1
                fn_end = j - 1

            fn_len = fn_end - fn_start + 1

            # Rule: Short function (<= 20 lines) is KEPT VERBATIM for single-turn comprehension!
            if fn_len <= 20:
                kept_fn += 1
                for k in range(fn_start, fn_end + 1):
                    out_lines.append(lines[k])
                i = fn_end + 1
                continue
            else:
                # Large function (> 20 lines): Keep signature + docstring, fold body!
                folded_fn += 1
                out_lines.append(lines[fn_start])
                
                # Check for docstring on next lines
                body_start = fn_start + 1
                if body_start < len(lines):
                    next_c = lines[body_start].split("|", 1)[-1] if "|" in lines[body_start] else lines[body_start]
                    if '"""' in next_c or "/*" in next_c or next_c.strip().startswith("//"):
                        out_lines.append(lines[body_start])
                        body_start += 1
                
                # Folded stub with line numbers
                start_num = fn_start + 1
                end_num = fn_end + 1
                stub_prefix = prefix if prefix else ""
                out_lines.append(f"{stub_prefix}{' ' * (indent + 4)}[tt:folded L{start_num}-L{end_num} ({fn_len}L)]")
                if is_brace_lang and lines[fn_end].strip().endswith("}"):
                    # Keep closing brace for brace languages
                    out_lines.append(lines[fn_end])

                i = fn_end + 1
                continue

        out_lines.append(line)
        i += 1

    res = "\n".join(out_lines)
    before_len = len(text)
    after_len = len(res)
    return res, {
        "gate": "ok",
        "before_chars": before_len,
        "after_chars": after_len,
        "saved_chars": max(0, before_len - after_len),
        "kept_functions": kept_fn,
        "folded_functions": folded_fn,
    }
