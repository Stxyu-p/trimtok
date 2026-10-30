"""Multi-language AST code skeletonizer using tree-sitter.

Falls back to Python's built-in ast for .py files when tree-sitter unavailable.
"""
import os
import re

_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
}

_TS_LANGS = {}


def _get_ts_lang(lang: str):
    if lang in _TS_LANGS:
        return _TS_LANGS[lang]
    try:
        if lang == "python":
            import tree_sitter_python as mod
        elif lang == "javascript":
            import tree_sitter_javascript as mod
        elif lang == "go":
            import tree_sitter_go as mod
        elif lang == "rust":
            import tree_sitter_rust as mod
        elif lang == "java":
            import tree_sitter_java as mod
        elif lang == "c":
            import tree_sitter_c as mod
        elif lang == "cpp":
            import tree_sitter_cpp as mod
        else:
            return None
        from tree_sitter import Language, Parser
        language = Language(mod.language())
        parser = Parser(language)
        _TS_LANGS[lang] = (language, parser)
        return _TS_LANGS[lang]
    except Exception:
        return None


def detect_language(code: str, filename: str = "") -> str:
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext in _LANG_MAP:
            return _LANG_MAP[ext]
    # Heuristic from content
    if "def " in code and "import " in code:
        return "python"
    if "function " in code or "const " in code or "=>" in code:
        return "javascript"
    if "func " in code and "package " in code:
        return "go"
    if "fn " in code and "let mut" in code:
        return "rust"
    if "public class" in code or "public static void main" in code:
        return "java"
    return "unknown"


def _skeletonize_ts(code: str, lang: str, min_body_lines: int = 8) -> str:
    """Extract signatures and fold bodies using tree-sitter."""
    ts = _get_ts_lang(lang)
    if ts is None:
        return code
    _, parser = ts
    try:
        tree = parser.parse(code.encode("utf-8"))
    except Exception:
        return code

    lines = code.split("\n")
    n = len(lines)

    # Collect ranges to fold: (start_line_1idx, end_line_1idx, indent, name)
    fold_ranges = []

    def _walk(node, depth=0):
        if node is None:
            return
        # Look for function/method definitions
        node_type = node.type
        is_func = node_type in (
            "function_definition", "function_declaration", "method_definition",
            "method_declaration", "function_item", "func_declaration",
            "constructor_declaration", "arrow_function",
        )
        if is_func:
            start = node.start_point[0] + 1  # 1-indexed
            end = node.end_point[0] + 1
            # Find body
            body = None
            for child in node.children:
                if child.type in ("block", "statement_block", "compound_statement",
                                  "function_body", "constructor_body", "body"):
                    body = child
                    break
            if body is not None:
                # Find first and last statement children (skip braces)
                stmts = [c for c in body.children
                         if c.type not in ("{", "}", "comment")]
                if not stmts:
                    pass
                else:
                    body_start = stmts[0].start_point[0] + 1
                    body_end = stmts[-1].end_point[0] + 1
                    span = body_end - body_start + 1
                    if span >= min_body_lines:
                        # Get function name
                        name = "<anon>"
                        for child in node.children:
                            if child.type in ("identifier", "property_identifier", "name"):
                                name = code[child.start_byte:child.end_byte]
                                break
                        # Get indent
                        line_text = lines[body_start - 1] if body_start - 1 < n else ""
                        indent = len(line_text) - len(line_text.lstrip())
                        indent_str = " " * indent
                        fold_ranges.append((body_start, body_end, indent_str, name))
        for child in node.children:
            _walk(child, depth + 1)

    _walk(tree.root_node)

    if not fold_ranges:
        return code

    # Sort and drop overlapping
    fold_ranges.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    merged = []
    last_end = 0
    for s, e, ind, fn in fold_ranges:
        if s <= last_end:
            continue
        merged.append((s, e, ind, fn))
        last_end = e

    # Build output
    out = []
    cur = 1
    for s, e, ind, fn in merged:
        while cur < s and cur <= n:
            out.append(lines[cur - 1])
            cur += 1
        omitted = e - s + 1
        marker = f"{ind}pass  # [tt:body L{s}-L{e} ({omitted}L of {fn}) -> read_file(offset={s}, limit={omitted})]"
        out.append(marker)
        cur = e + 1

    while cur <= n:
        out.append(lines[cur - 1])
        cur += 1

    new_text = "\n".join(out)
    if len(new_text) >= len(code):
        return code
    return new_text


def skeletonize_code_multilang(code: str, filename: str = "", min_body_lines: int = 8) -> str:
    """Skeletonize code in any supported language. Falls back to original if no savings."""
    lang = detect_language(code, filename)
    if lang == "python":
        # Use existing Python ast-based skeletonizer
        try:
            from .pruners import smart_skeletonize_code
            result, saved, _ = smart_skeletonize_code(code)
            if saved > 0:
                return result
        except Exception:
            pass
    if lang in ("python", "javascript", "go", "rust", "java", "c", "cpp"):
        result = _skeletonize_ts(code, lang, min_body_lines)
        if result != code and len(result) < len(code):
            return result
    return code