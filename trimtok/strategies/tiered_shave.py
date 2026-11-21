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
from ..signals import digest as _digest_fn

_LINE_PREFIX_RE = re.compile(r"^(\s*(\d+)\|)(.*)$")
_PY_FN_RE = re.compile(r"^\s*(?:async\s+)?def\s+([a-zA-Z0-9_]+)\s*\(")
_JS_FN_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_]+)\s*\(")
_GO_FN_RE = re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)\s*\(")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

_WEB_NOISE_RE = re.compile(
    r"(?i)(?:cookie\s+(?:notice|policy|settings)|accept\s+all|reject\s+non-essential|"
    r"navigation:\s+home\s*>|share\s+on\s+(?:twitter|x|facebook|linkedin)|"
    r"subscribe\s+to\s+our\s+newsletter|advertisement:)"
)
_LINK_DEBLOAT_RE = re.compile(
    r"\[([^\]\n]{1,120})\]\((?:https?://|/)[^)\"\s]+(?:\s+['\"][^'\"]*['\"])?\)"
)


def _strip_line_prefix(line: str) -> tuple[str, int | None, str]:
    m = _LINE_PREFIX_RE.match(line)
    if m:
        return m.group(1), int(m.group(2)), m.group(3)
    return "", None, line


def _is_web_or_doc(domain: str, lang: str) -> bool:
    return domain in ("web-doc", "spec-doc") or lang in ("markdown", "text", "html")


def shave_tiered(text: str, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Apply tiered semantic shaving with single-turn comprehension guarantee."""
    meta = meta or {}
    domain = meta.get("domain", "")
    lang = (meta.get("lang") or "").lower()

    clean_text, min_stats = minify_lossless(text, meta)

    if domain == "code-small" or (domain != "code-large" and not _is_web_or_doc(domain, lang) and len(text) < 14_000):
        return clean_text, min_stats

    lines = clean_text.split("\n")
    out_lines: list[str] = []
    kept_fn = 0
    folded_fn = 0

    # Case A: Web or Document
    if _is_web_or_doc(domain, lang):
        filtered_lines = []
        in_fence = False
        fence_char = ""
        fence_len = 0
        for line in lines:
            pfx, _, content = _strip_line_prefix(line)
            fm = _FENCE_RE.match(content)
            if fm:
                tok = fm.group(1)
                if not in_fence:
                    in_fence, fence_char, fence_len = True, tok[0], len(tok)
                elif tok[0] == fence_char and len(tok) >= fence_len:
                    in_fence, fence_char, fence_len = False, "", 0
                filtered_lines.append(line)
                continue
            if in_fence:
                filtered_lines.append(line)
                continue
            if _WEB_NOISE_RE.search(content):
                continue
            # Strip URL targets from inline markdown links outside code fences only
            debloated = _LINK_DEBLOAT_RE.sub(r"\1", content)
            filtered_lines.append(f"{pfx}{debloated}" if pfx else debloated)
        interim_text = "\n".join(filtered_lines)

        if len(interim_text) >= 2000:
            from ..dedup import _unwrap_numbered_lines
            raw_body, prefixes, line_nos, _, _ = _unwrap_numbered_lines(interim_text)
            _ref = str(meta.get("ref") or "").strip()
            if not _ref:
                _ref = f"#{_digest_fn(text)}"
            elif not _ref.startswith("#"):
                _ref = f"#{_ref}"
            folded_doc, d_saved = fold_text_structure(
                raw_body,
                ref=_ref,
                line_prefixes=prefixes,
                line_nos=line_nos,
                allow_read_file_hint=not bool(meta.get("no_read_file", False)),
            )
            if d_saved > 200:
                interim_text = folded_doc

        before_len = len(text)
        if len(interim_text) >= before_len:
            return text, {
                "gate": "passthrough_no_gain",
                "before_chars": before_len,
                "after_chars": before_len,
                "saved_chars": 0,
                "kept_functions": 0,
                "folded_functions": 0,
            }
        after_len = len(interim_text)
        return interim_text, {
            "gate": "ok",
            "before_chars": before_len,
            "after_chars": after_len,
            "saved_chars": before_len - after_len,
            "kept_functions": 0,
            "folded_functions": 0,
        }

    # Case B: Large Code (> 14KB)
    is_brace_lang = lang in ("javascript", "typescript", "go", "c", "cpp", "rust", "java")

    i = 0
    while i < len(lines):
        line = lines[i]
        prefix, start_lno, content = _strip_line_prefix(line)
        stripped = content.lstrip()
        indent = len(content) - len(stripped)

        if is_brace_lang:
            fn_match = bool(_JS_FN_RE.match(stripped) or _GO_FN_RE.match(stripped))
        else:
            fn_match = bool(_PY_FN_RE.match(stripped))

        if fn_match:
            fn_start = i
            fn_end = i
            if is_brace_lang:
                brace_cnt = content.count("{") - content.count("}")
                j = i + 1
                while j < len(lines) and brace_cnt > 0:
                    _, _, c = _strip_line_prefix(lines[j])
                    brace_cnt += c.count("{") - c.count("}")
                    j += 1
                fn_end = j - 1
            else:
                j = i + 1
                while j < len(lines):
                    _, _, next_content = _strip_line_prefix(lines[j])
                    next_strip = next_content.strip()
                    next_indent = len(next_content) - len(next_content.lstrip())
                    if next_strip and next_indent <= indent:
                        break
                    j += 1
                fn_end = j - 1

            fn_len = fn_end - fn_start + 1

            if fn_len <= 20:
                kept_fn += 1
                for k in range(fn_start, fn_end + 1):
                    out_lines.append(lines[k])
                i = fn_end + 1
                continue
            else:
                folded_fn += 1
                out_lines.append(lines[fn_start])

                body_start = fn_start + 1
                if body_start < len(lines):
                    _, _, next_c = _strip_line_prefix(lines[body_start])
                    if '"""' in next_c or "/*" in next_c or next_c.strip().startswith("//"):
                        out_lines.append(lines[body_start])
                        body_start += 1

                _, end_lno, _ = _strip_line_prefix(lines[fn_end])
                start_num = start_lno if start_lno is not None else (fn_start + 1)
                end_num = end_lno if end_lno is not None else (fn_end + 1)
                # Never masquerade as original code line prefix on a synthetic stub
                out_lines.append(f"{' ' * (indent + 4)}[tt:folded L{start_num}-L{end_num} ({fn_len}L)]")
                if is_brace_lang and lines[fn_end].strip().endswith("}"):
                    out_lines.append(lines[fn_end])

                i = fn_end + 1
                continue

        out_lines.append(line)
        i += 1

    res = "\n".join(out_lines)
    before_len = len(text)
    if len(res) >= before_len:
        return text, {
            "gate": "passthrough_no_gain",
            "before_chars": before_len,
            "after_chars": before_len,
            "saved_chars": 0,
            "kept_functions": kept_fn,
            "folded_functions": 0,
        }
    after_len = len(res)
    return res, {
        "gate": "ok",
        "before_chars": before_len,
        "after_chars": after_len,
        "saved_chars": before_len - after_len,
        "kept_functions": kept_fn,
        "folded_functions": folded_fn,
    }
