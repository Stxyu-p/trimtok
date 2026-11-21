"""Multi-language AST code skeletonizer using tree-sitter.

Extracts signatures, classes, interfaces, and folds large function bodies.
Supports Python, JavaScript/TypeScript, Go, Rust, Java, C, C++, C#, and Bash.
All operations are 100% fail-open, non-recursive, thread-safe, patch-safe, and never raise.
"""
import os
import threading
from typing import Any

_LANG_MAP = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".mts": "javascript",
    ".cts": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
}

_TS_LANGS: dict[str, Any] = {}
_TS_LANG_LOCK = threading.Lock()
_TS_LOCAL = threading.local()


def _get_ts_lang(lang: str):
    """Load immutable Language once and return a thread-local Parser (TSParser is not thread-safe)."""
    with _TS_LANG_LOCK:
        language = _TS_LANGS.get(lang, False)
    if language is False:
        loaded = None
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
            elif lang == "csharp":
                import tree_sitter_c_sharp as mod
            elif lang == "bash":
                import tree_sitter_bash as mod

            if mod is not None:
                from tree_sitter import Language

                loaded = Language(mod.language())
        except Exception:
            loaded = None
        with _TS_LANG_LOCK:
            _TS_LANGS[lang] = loaded
        language = loaded

    if language is None:
        return None

    parsers = getattr(_TS_LOCAL, "parsers", None)
    if parsers is None:
        parsers = {}
        _TS_LOCAL.parsers = parsers
    parser = parsers.get(lang)
    if parser is None:
        try:
            from tree_sitter import Parser

            parser = Parser(language)
            parsers[lang] = parser
        except Exception:
            return None
    return language, parser


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
        "int main(" in code or "void main(" in code or "void " in code or "class " in code or "std::" in code
    ):
        return "cpp" if "std::" in code or "class " in code else "c"
    if ("public class " in code or "public static void main" in code or "package " in code) and "System.out" in code:
        return "java"
    if ("public " in code or "class " in code) and ("namespace " in code and "using " in code):
        return "csharp"
    first_line = code.splitlines()[0].strip() if code else ""
    if first_line.startswith("#!") and any(sh in first_line for sh in ("/sh", "/bash", "/zsh", " sh", " bash", " zsh")):
        return "bash"
    if ("function " in code or "const " in code or "=>" in code or "let " in code) and (
        "console." in code or "require(" in code or "export " in code or "{" in code
    ):
        return "javascript"
    return "unknown"


def _extract_ts_func_name(node: Any, raw_bytes: bytes) -> str:
    """Extract function/method name across Python, JS, Go, Rust, Java, C/C++, C#, and Bash."""
    for child in node.children:
        ctype = child.type
        if ctype in ("identifier", "property_identifier", "name", "field_identifier", "word"):
            return raw_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        if ctype in ("function_declarator", "pointer_declarator", "reference_declarator"):
            sub = _extract_ts_func_name(child, raw_bytes)
            if sub != "<anon>":
                return sub
    return "<anon>"


def _skeletonize_ts_core(
    code: str,
    lang: str = "javascript",
    min_body_lines: int = 8,
    ref: str = "",
    line_prefixes: list[str] | None = None,
    line_nos: list[int] | None = None,
    focus_lines: set[int] | list[int] | None = None,
    deadline: Any | None = None,
) -> tuple[str, int, int]:
    """Extract signatures and fold function bodies using tree-sitter. Returns (text, saved_chars, count)."""
    ts = _get_ts_lang(lang)
    if ts is None:
        return code, 0, 0
    _, parser = ts
    try:
        if deadline is not None:
            deadline.check()
        raw_bytes = code.encode("utf-8", errors="replace")
        tree = parser.parse(raw_bytes)
    except TimeoutError:
        raise
    except Exception:
        return code, 0, 0

    if tree is None or tree.root_node is None:
        return code, 0, 0

    lines = code.split("\n")
    total_lines = len(lines)
    use_prefixes = (
        line_prefixes is not None
        and line_nos is not None
        and len(line_prefixes) == total_lines
        and len(line_nos) == total_lines
    )

    fold_ranges = []
    focus_set = set(focus_lines) if focus_lines else None
    is_python = (lang == "python")

    stack = [tree.root_node]
    steps = 0
    while stack:
        steps += 1
        if deadline is not None and (steps & 255) == 0:
            deadline.check()
        node = stack.pop()
        node_type = node.type
        is_func = node_type in (
            "function_definition", "function_declaration", "method_definition",
            "method_declaration", "function_item", "func_declaration",
            "constructor_declaration", "arrow_function",
        )
        if is_func:
            fn_node_s = node.start_point[0] + 1
            fn_node_e = node.end_point[0] + 1
            skip_this_fold = False
            if focus_set and fn_node_s and fn_node_e:
                if use_prefixes and line_nos:
                    r_s = line_nos[fn_node_s - 1] if 0 <= fn_node_s - 1 < len(line_nos) else fn_node_s
                    r_e = line_nos[fn_node_e - 1] if 0 <= fn_node_e - 1 < len(line_nos) else fn_node_e
                else:
                    r_s, r_e = fn_node_s, fn_node_e
                if any(r_s <= fl <= r_e for fl in focus_set):
                    # Skip folding this outer node, but STILL descend into children to fold unfocused inner functions
                    skip_this_fold = True

            if not skip_this_fold:
                body = None
                for child in node.children:
                    if child.type in (
                        "block", "statement_block", "compound_statement",
                        "function_body", "constructor_body", "body",
                    ):
                        body = child
                        break
                if body is not None:
                    stmts = []
                    for c in body.children:
                        if c.type == "statement_list":
                            stmts.extend([sc for sc in c.children if sc.type not in ("{", "}", "comment")])
                        elif c.type not in ("{", "}", "comment"):
                            stmts.append(c)
                    if stmts:
                        body_start = stmts[0].start_point[0] + 1
                        body_end = stmts[-1].end_point[0] + 1
                        # Protect inline opening signature / `{` and closing `}` lines from deletion
                        if body_start == fn_node_s or (not is_python and body_start == body.start_point[0] + 1):
                            body_start += 1
                        if not is_python and body_end == body.end_point[0] + 1:
                            body_end -= 1
                        span = body_end - body_start + 1
                        if span >= min_body_lines:
                            name = _extract_ts_func_name(node, raw_bytes)
                            line_text = lines[body_start - 1] if 0 <= body_start - 1 < total_lines else ""
                            # Preserve original tabs/spaces verbatim (prevents Python TabError)
                            indent_str = line_text[: len(line_text) - len(line_text.lstrip())]
                            fold_ranges.append((body_start, body_end, indent_str, name))

        for child in reversed(node.children):
            stack.append(child)

    if not fold_ranges:
        return code, 0, 0

    fold_ranges.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    merged = []
    last_end = 0
    for s, e, ind, fn in fold_ranges:
        if s <= last_end:
            continue
        merged.append((s, e, ind, fn))
        last_end = e

    if not merged:
        return code, 0, 0

    ref_tag = f" {ref}" if ref else ""
    out = []
    cur = 1
    for s, e, ind, fn in merged:
        while cur < s and cur <= total_lines:
            prefix = line_prefixes[cur - 1] if use_prefixes else ""
            out.append(f"{prefix}{lines[cur - 1]}")
            cur += 1
        file_s = line_nos[s - 1] if use_prefixes else s
        file_e = line_nos[e - 1] if use_prefixes else e
        omitted = file_e - file_s + 1
        marker_prefix = line_prefixes[s - 1] if use_prefixes else ""
        if is_python:
            marker = f"{marker_prefix}{ind}pass  # [tt:body L{file_s}-L{file_e} ({omitted}L of {fn}){ref_tag} -> read_file(offset={file_s}, limit={omitted})]"
        else:
            marker = f"{marker_prefix}{ind}// [tt:body L{file_s}-L{file_e} ({omitted}L of {fn}){ref_tag} -> read_file(offset={file_s}, limit={omitted})]"
        out.append(marker)
        cur = e + 1

    while cur <= total_lines:
        prefix = line_prefixes[cur - 1] if use_prefixes else ""
        out.append(f"{prefix}{lines[cur - 1]}")
        cur += 1

    new_text = "\n".join(out)
    if use_prefixes:
        orig_len = sum(len(line_prefixes[i]) + len(lines[i]) + 1 for i in range(total_lines)) - 1
    else:
        orig_len = len(code)
    saved = orig_len - len(new_text)
    if saved <= 0:
        return code, 0, 0
    return new_text, saved, len(merged)


def skeletonize_code_full(
    code: str,
    filename: str = "",
    ref: str = "",
    line_prefixes: list[str] | None = None,
    line_nos: list[int] | None = None,
    min_body_lines: int = 8,
    focus_lines: set[int] | list[int] | None = None,
    deadline: Any | None = None,
) -> tuple[str, int, int]:
    """Full multi-language code skeletonizer with exact line prefixes and line numbers."""
    if not code or len(code) < 512:
        return code, 0, 0
    lang = detect_language(code, filename)
    if lang == "python":
        try:
            from .pruners import smart_skeletonize_code

            # Clean AST parse succeeded: return directly (don't fall through to tree-sitter on intentional saved==0)
            return smart_skeletonize_code(
                code, ref=ref, min_body_lines=min_body_lines,
                line_prefixes=line_prefixes, line_nos=line_nos,
                focus_lines=focus_lines, deadline=deadline,
            )
        except TimeoutError:
            raise
        except Exception:
            pass
    if lang in ("python", "javascript", "go", "rust", "java", "c", "cpp", "csharp", "bash"):
        return _skeletonize_ts_core(
            code, lang=lang, min_body_lines=min_body_lines, ref=ref,
            line_prefixes=line_prefixes, line_nos=line_nos,
            focus_lines=focus_lines, deadline=deadline,
        )
    return code, 0, 0


def skeletonize_code_multilang(code: str, filename: str = "", min_body_lines: int = 8) -> str:
    """Skeletonize code in any supported language without line numbers. Falls back to original if no savings."""
    res, saved, _ = skeletonize_code_full(code, filename=filename, min_body_lines=min_body_lines)
    return res if saved > 0 else code
