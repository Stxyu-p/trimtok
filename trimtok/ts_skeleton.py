"""Multi-language AST code skeletonizer using tree-sitter.

Extracts signatures, classes, interfaces, and folds large function bodies.
Falls back to Python's built-in ast for .py files when tree-sitter unavailable.
All operations are 100% fail-open, non-recursive, and never raise.
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

_TS_LANGS: dict = {}


def _get_ts_lang(lang: str):
    """Load and cache tree-sitter language parser. Caches None on failure."""
    if lang in _TS_LANGS:
        return _TS_LANGS[lang]
    try:
        mod = None
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

        if mod is None:
            _TS_LANGS[lang] = None
            return None

        from tree_sitter import Language, Parser
        language = Language(mod.language())
        parser = Parser(language)
        _TS_LANGS[lang] = (language, parser)
        return _TS_LANGS[lang]
    except Exception:
        _TS_LANGS[lang] = None
        return None


def detect_language(code: str, filename: str = "") -> str:
    """Detect programming language from file extension or content patterns."""
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext in _LANG_MAP:
            return _LANG_MAP[ext]
    if not code or not isinstance(code, str):
        return "unknown"
    # Content-based heuristics (check unique language markers first)
    if "def " in code and ("import " in code or "self" in code or "class " in code):
        return "python"
    if "fn " in code and ("pub fn " in code or "let mut " in code or "impl " in code or "use " in code or "->" in code):
        return "rust"
    if "func " in code and ("package " in code or "import (" in code):
        return "go"
    if ("#include <" in code or '#include "' in code) and (
        "int main(" in code or "void " in code or "class " in code or "std::" in code
    ):
        return "cpp" if "std::" in code or "class " in code else "c"
    if ("public class " in code or "public static void main" in code or "package " in code) and "System.out" in code:
        return "java"
    if ("function " in code or "const " in code or "=>" in code or "let " in code) and (
        "console." in code or "require(" in code or "export " in code or "{" in code
    ):
        return "javascript"
    return "unknown"


def _skeletonize_ts(code: str, lang: str, min_body_lines: int = 8) -> str:
    """Extract signatures and fold function bodies using tree-sitter. Iterative & fail-open."""
    ts = _get_ts_lang(lang)
    if ts is None:
        return code
    _, parser = ts
    try:
        raw_bytes = code.encode("utf-8", errors="replace")
        tree = parser.parse(raw_bytes)
    except Exception:
        return code

    if tree is None or tree.root_node is None:
        return code

    lines = code.split("\n")
    n = len(lines)

    # Collect ranges to fold: (start_line_1idx, end_line_1idx, indent_str, name)
    fold_ranges = []

    # Iterative depth-first traversal to avoid recursion limits
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        node_type = node.type
        is_func = node_type in (
            "function_definition", "function_declaration", "method_definition",
            "method_declaration", "function_item", "func_declaration",
            "constructor_declaration", "arrow_function",
        )
        if is_func:
            body = None
            for child in node.children:
                if child.type in (
                    "block", "statement_block", "compound_statement",
                    "function_body", "constructor_body", "body"
                ):
                    body = child
                    break
            if body is not None:
                stmts = [c for c in body.children if c.type not in ("{", "}", "comment")]
                if stmts:
                    body_start = stmts[0].start_point[0] + 1
                    body_end = stmts[-1].end_point[0] + 1
                    span = body_end - body_start + 1
                    if span >= min_body_lines:
                        name = "<anon>"
                        for child in node.children:
                            if child.type in ("identifier", "property_identifier", "name", "field_identifier"):
                                name = code[child.start_byte:child.end_byte]
                                break
                        line_text = lines[body_start - 1] if body_start - 1 < n else ""
                        indent = len(line_text) - len(line_text.lstrip())
                        indent_str = " " * indent
                        fold_ranges.append((body_start, body_end, indent_str, name))

        # Push children in reverse order to preserve original document traversal order
        for child in reversed(node.children):
            stack.append(child)

    if not fold_ranges:
        return code

    # Sort ranges: ascending start line, descending span
    fold_ranges.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    merged = []
    last_end = 0
    for s, e, ind, fn in fold_ranges:
        if s <= last_end:
            continue
        merged.append((s, e, ind, fn))
        last_end = e

    if not merged:
        return code

    # Build folded output
    is_python = (lang == "python")
    out = []
    cur = 1
    for s, e, ind, fn in merged:
        while cur < s and cur <= n:
            out.append(lines[cur - 1])
            cur += 1
        omitted = e - s + 1
        if is_python:
            marker = f"{ind}pass  # [tt:body L{s}-L{e} ({omitted}L of {fn}) -> read_file(offset={s}, limit={omitted})]"
        else:
            marker = f"{ind}// [tt:body L{s}-L{e} ({omitted}L of {fn}) -> read_file(offset={s}, limit={omitted})]"
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
    if not code or len(code) < 512:
        return code
    lang = detect_language(code, filename)
    if lang == "python":
        # First try Python's standard ast pruner
        try:
            from .pruners import smart_skeletonize_code
            result, saved, _ = smart_skeletonize_code(code)
            if saved > 0 and len(result) < len(code):
                return result
        except Exception:
            pass
    if lang in ("python", "javascript", "go", "rust", "java", "c", "cpp"):
        result = _skeletonize_ts(code, lang, min_body_lines)
        if result != code and len(result) < len(code):
            return result
    return code
