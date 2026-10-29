"""RTK / Repomix pure signature stripping strategy (Baseline Strategy A).

Completely strips function/method bodies, preserving only signatures, imports,
interfaces, and type declarations.
"""
from __future__ import annotations

import re
from typing import Any

_LINE_PREFIX_RE = re.compile(r"^(\s*\d+\|)(.*)$")
_PY_FN_RE = re.compile(r"^\s*(?:async\s+)?def\s+([a-zA-Z0-9_]+)\s*\(")
_JS_FN_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_]+)\s*\(")
_GO_FN_RE = re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)\s*\(")

def strip_signatures_rtk(text: str, meta: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Strip all function bodies down to signatures (RTK -l aggressive behavior)."""
    meta = meta or {}
    lang = (meta.get("lang") or "").lower()
    lines = text.split("\n")
    out_lines: list[str] = []
    
    in_function = False
    fn_indent = 0
    folded_count = 0
    
    # Brace-based state for JS/TS/Go
    brace_depth = 0
    is_brace_lang = lang in ("javascript", "typescript", "go", "c", "cpp", "rust", "java")

    for idx, line in enumerate(lines):
        m = _LINE_PREFIX_RE.match(line)
        prefix = m.group(1) if m else ""
        content = m.group(2) if m else line
        stripped_content = content.lstrip()
        indent = len(content) - len(stripped_content)

        if is_brace_lang:
            if not in_function:
                if _JS_FN_RE.match(stripped_content) or _GO_FN_RE.match(stripped_content):
                    in_function = True
                    folded_count += 1
                    brace_depth = content.count("{") - content.count("}")
                    if "{" in content:
                        sig_part = content.split("{")[0].rstrip()
                        out_lines.append(f"{prefix}{sig_part} {{ ... }}")
                    else:
                        out_lines.append(f"{prefix}{content}")
                    continue
                out_lines.append(line)
            else:
                brace_depth += content.count("{") - content.count("}")
                if brace_depth <= 0:
                    in_function = False
                    brace_depth = 0
                # Omit body lines
                continue
        else:
            # Python or indentation-based
            if not in_function:
                if _PY_FN_RE.match(stripped_content):
                    in_function = True
                    fn_indent = indent
                    folded_count += 1
                    out_lines.append(line)
                    # Add a stub representation
                    out_lines.append(f"{prefix}{' ' * (indent + 4)}...")
                    continue
                out_lines.append(line)
            else:
                if stripped_content and indent <= fn_indent:
                    in_function = False
                    if _PY_FN_RE.match(stripped_content):
                        in_function = True
                        fn_indent = indent
                        folded_count += 1
                        out_lines.append(line)
                        out_lines.append(f"{prefix}{' ' * (indent + 4)}...")
                        continue
                    out_lines.append(line)
                # Skip inside function body

    res = "\n".join(out_lines)
    before_len = len(text)
    after_len = len(res)
    return res, {
        "gate": "ok",
        "before_chars": before_len,
        "after_chars": after_len,
        "saved_chars": max(0, before_len - after_len),
        "kept_functions": 0,
        "folded_functions": folded_count,
    }
