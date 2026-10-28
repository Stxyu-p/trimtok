"""trimtok pruners: semantic diff compression, stack-trace pruning, scoped search grouping, and AST code skeletonization."""

from __future__ import annotations

import ast
import json as _json
import re
from typing import Any

try:
    from .signals import is_important, seen_lines
except ImportError:
    from signals import is_important, seen_lines


# ---------------------------------------------------------------------------
# 1. Unified Diff Pruner (diff_engine)
# ---------------------------------------------------------------------------
_DIFF_GIT_RE = re.compile(r"^diff --git a/.* b/.*")
_HUNK_HDR_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@.*", re.M)
_FILE_HDR_PREFIXES = ("--- ", "+++ ", "index ", "new file mode", "deleted file mode", "similarity index", "rename from", "rename to")

_MIN_UNCHANGED_RUN = 6
_KEEP_CONTEXT = 2


def prune_unified_diff(text: str, budget: int = 200) -> str:
    """Compress unified diff text semantically without breaking hunk structures."""
    try:
        if not text or ("diff --git" not in text and not _HUNK_HDR_RE.search(text)):
            return text

        lines = text.split("\n")
        out: list[str] = []
        unchanged_buf: list[str] = []

        def _flush_unchanged() -> None:
            nonlocal unchanged_buf
            if not unchanged_buf:
                return
            n = len(unchanged_buf)
            if n <= _MIN_UNCHANGED_RUN:
                out.extend(unchanged_buf)
            else:
                out.extend(unchanged_buf[:_KEEP_CONTEXT])
                omitted = n - (_KEEP_CONTEXT * 2)
                out.append(f"  [... {omitted} unchanged lines ...]")
                out.extend(unchanged_buf[-_KEEP_CONTEXT:])
            unchanged_buf = []

        for line in lines:
            if _DIFF_GIT_RE.match(line) or any(line.startswith(p) for p in _FILE_HDR_PREFIXES):
                _flush_unchanged()
                out.append(line)
            elif _HUNK_HDR_RE.match(line):
                _flush_unchanged()
                out.append(line)
            elif line.startswith("+") or line.startswith("-"):
                _flush_unchanged()
                out.append(line)
            elif line.startswith(" ") or line == "":
                unchanged_buf.append(line)
            else:
                _flush_unchanged()
                out.append(line)

        _flush_unchanged()
        if budget > 0 and len(out) > budget:
            head_keep = max(1, budget - max(2, budget // 5))
            tail_keep = max(1, budget - head_keep)
            omitted_lines = len(out) - head_keep - tail_keep
            if omitted_lines > 0:
                out = (
                    out[:head_keep]
                    + [f"  [... {omitted_lines} diff lines omitted ...]"]
                    + out[-tail_keep:]
                )
        res = "\n".join(out)
        return res if len(res) < len(text) else text
    except Exception:
        return text


# ---------------------------------------------------------------------------
# 2. Traceback Pruner (traceback_pruner)
# ---------------------------------------------------------------------------
_PY_TB_START = re.compile(r"^\s*Traceback \(most recent call last\):")
_PY_FRAME_RE = re.compile(r"^  File \"([^\"]+)\", line (\d+), in (.*)$")
_JS_FRAME_RE = re.compile(r"^\s+at\s+(?:(?:new\s+)?([^\s(]+)\s+\()?(.*?)(?::(\d+):(\d+))?\)?$")

_FRAMEWORK_PATH_RE = re.compile(
    r"(?i)(?:site-packages|dist-packages|node_modules|<frozen|lib[/\\]python\d|"
    r"unittest[/\\]case\.py|_pytest[/\\]|pluggy[/\\]|starlette[/\\]|"
    r"fastapi[/\\]|click[/\\]|tornado[/\\]|flask[/\\]|urllib3[/\\]|requests[/\\])"
)

MIN_FRAMEWORK_RUN = 2


def _is_framework_path(path: str) -> bool:
    return bool(_FRAMEWORK_PATH_RE.search(path or ""))


def prune_python_traceback(lines: list[str]) -> list[str]:
    """Parse and prune Python traceback blocks within a line list."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not _PY_TB_START.match(line):
            out.append(line)
            i += 1
            continue

        tb_head = line
        i += 1
        frames: list[tuple[str, str, bool]] = []
        while i < n:
            m = _PY_FRAME_RE.match(lines[i])
            if m:
                hdr = lines[i]
                code = ""
                i += 1
                if i < n and (lines[i].startswith("    ") or lines[i].startswith("\t")):
                    code = lines[i]
                    i += 1
                is_fw = _is_framework_path(m.group(1))
                frames.append((hdr, code, is_fw))
            else:
                break

        if len(frames) <= 3:
            out.append(tb_head)
            for hdr, code, _ in frames:
                out.append(hdr)
                if code:
                    out.append(code)
            continue

        kept_blocks: list[tuple[str, str] | str] = []
        kept_blocks.append((frames[0][0], frames[0][1]))

        fw_run = 0
        j = 1
        m_frames = len(frames) - 1
        while j < m_frames:
            hdr, code, is_fw = frames[j]
            if is_fw:
                fw_run += 1
                j += 1
                continue
            if fw_run > 0:
                if fw_run >= MIN_FRAMEWORK_RUN:
                    kept_blocks.append(f"  [... {fw_run} framework frames omitted ...]")
                else:
                    for k in range(j - fw_run, j):
                        kept_blocks.append((frames[k][0], frames[k][1]))
                fw_run = 0
            kept_blocks.append((hdr, code))
            j += 1

        if fw_run > 0:
            if fw_run >= MIN_FRAMEWORK_RUN:
                kept_blocks.append(f"  [... {fw_run} framework frames omitted ...]")
            else:
                for k in range(m_frames - fw_run, m_frames):
                    kept_blocks.append((frames[k][0], frames[k][1]))

        kept_blocks.append((frames[-1][0], frames[-1][1]))

        out.append(tb_head)
        for item in kept_blocks:
            if isinstance(item, str):
                out.append(item)
            else:
                out.append(item[0])
                if item[1]:
                    out.append(item[1])

    return out


def prune_js_traceback(lines: list[str]) -> list[str]:
    """Parse and prune Node/JS traceback lines."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not _JS_FRAME_RE.match(line):
            out.append(line)
            i += 1
            continue

        frames: list[tuple[str, bool]] = []
        while i < n and _JS_FRAME_RE.match(lines[i]):
            hdr = lines[i]
            is_fw = _is_framework_path(hdr)
            frames.append((hdr, is_fw))
            i += 1

        if len(frames) <= 3:
            for hdr, _ in frames:
                out.append(hdr)
            continue

        out.append(frames[0][0])
        fw_run = 0
        j = 1
        m_frames = len(frames) - 1
        while j < m_frames:
            hdr, is_fw = frames[j]
            if is_fw:
                fw_run += 1
                j += 1
                continue
            if fw_run > 0:
                if fw_run >= MIN_FRAMEWORK_RUN:
                    out.append(f"    [... {fw_run} framework frames omitted ...]")
                else:
                    for k in range(j - fw_run, j):
                        out.append(frames[k][0])
                fw_run = 0
            out.append(hdr)
            j += 1
        if fw_run > 0:
            if fw_run >= MIN_FRAMEWORK_RUN:
                out.append(f"    [... {fw_run} framework frames omitted ...]")
            else:
                for k in range(m_frames - fw_run, m_frames):
                    out.append(frames[k][0])
        out.append(frames[-1][0])

    return out


def prune_traceback(text: str) -> str:
    """Entry point: inspect text and prune recognized framework frames."""
    try:
        if not text:
            return text
        has_py = "Traceback (most recent call last):" in text
        has_js = "\n    at " in text or "\n  at " in text or text.startswith("    at ") or text.startswith("  at ")
        if not has_py and not has_js:
            return text
        lines = text.split("\n")
        if has_py:
            lines = prune_python_traceback(lines)
        if has_js:
            lines = prune_js_traceback(lines)
        res = "\n".join(lines)
        return res if len(res) < len(text) else text
    except Exception:
        return text


prune_tracebacks = prune_traceback


# ---------------------------------------------------------------------------
# 3. Scoped Search Grouping (scoped_search)
# ---------------------------------------------------------------------------
_HIT_RE = re.compile(r"^([\w./\\~$@+:-]+?):(\d+):(.*)$")
_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|func(?:tion)?|fn)\s+([A-Za-z_$][\w$]*)"
)

_MIN_LINES = 12
_MIN_HITS = 10
_MIN_PATHS = 2
_HIT_FRACTION = 0.6
_KEEP_ALL = 3
_MIN_RUN = 2
_MAX_DEFS = 2


def looks_search_shaped(text: str) -> bool:
    """True when the doc is a multi-file path:line:text hit list."""
    try:
        lines = [ln for ln in (text or "").split("\n") if ln.strip()]
        if len(lines) < _MIN_LINES:
            return False
        hits = 0
        paths: set[str] = set()
        for ln in lines:
            m = _HIT_RE.match(ln)
            if m:
                hits += 1
                paths.add(m.group(1))
        if hits < _MIN_HITS or len(paths) < _MIN_PATHS:
            return False
        return hits / len(lines) >= _HIT_FRACTION
    except Exception:
        return False


def _render_segment(
    path: str,
    hits: list[tuple[int, str, str]],
    owner: str | None,
    deadline: Any | None,
) -> list[str]:
    raw = [h[2] for h in hits]
    n = len(hits)
    if n <= _KEEP_ALL:
        return raw
    keep = {0, n - 1}
    for i, (_, _, line) in enumerate(hits):
        if is_important(line):
            keep.add(i)
    defs: list[str] = []
    seen: set[str] = set()
    total_defs = 0
    for lineno, text, _ in hits:
        m = _DEF_RE.match(text)
        if m:
            total_defs += 1
            name = m.group(1)
            if name not in seen and len(defs) < _MAX_DEFS:
                seen.add(name)
                defs.append(f"{name}@L{lineno}")
    header = f"=== {path} ({n} hits)"
    if defs:
        extra = total_defs - len(defs)
        header += " [defs: " + " ".join(defs) + (f" …+{extra}" if extra > 0 else "") + "]"
    header += " ==="

    out: list[str] = []
    run: list[int] = []
    folded_any = False

    def _flush() -> None:
        nonlocal folded_any
        if not run:
            return
        if len(run) >= _MIN_RUN:
            out.append(seen_lines(len(run), owner or ""))
            folded_any = True
        else:
            out.extend(raw[k] for k in run)
        run.clear()

    for i in range(n):
        if deadline is not None and (i & 255) == 0:
            deadline.check()
        if i in keep:
            _flush()
            out.append(raw[i])
        else:
            run.append(i)
    _flush()
    if not folded_any:
        return raw
    return [header] + out


def group_search_hits(
    text: str,
    owner: str | None = None,
    deadline: Any | None = None,
) -> tuple[str, int]:
    """Collapse multi-file search hits into file-grouped blocks."""
    try:
        if not looks_search_shaped(text):
            return text, 0
        lines = text.split("\n")
        out: list[str] = []
        seg_path: str | None = None
        seg_hits: list[tuple[int, str, str]] = []

        def _flush_seg() -> None:
            nonlocal seg_path, seg_hits
            if seg_path is not None:
                out.extend(_render_segment(seg_path, seg_hits, owner, deadline))
            seg_path = None
            seg_hits = []

        for ln in lines:
            m = _HIT_RE.match(ln)
            if m:
                path = m.group(1)
                if seg_path is not None and path != seg_path:
                    _flush_seg()
                seg_path = path
                seg_hits.append((int(m.group(2)), m.group(3), ln))
            else:
                _flush_seg()
                out.append(ln)
        _flush_seg()
        new_text = "\n".join(out)
        saved = len(text) - len(new_text)
        if saved <= 0:
            return text, 0
        return new_text, saved
    except Exception:
        return text, 0


# ---------------------------------------------------------------------------
# 4. AST Code Skeletonizer (Language-Agnostic Structure Extractor)
# ---------------------------------------------------------------------------
_CODE_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<decl>(?:async\s+)?(?:def|class|function|fn|pub\s+fn|func|interface|struct|type)\s+[A-Za-z0-9_$]+[^{:]*[:{])"
)
_IMPORT_RE = re.compile(r"^[ \t]*(?:import|from|require|use|package|include)\b")


def skeletonize_code(code: str, max_body_lines: int = 5) -> str:
    """Extract signatures, docstrings, and imports while folding deep bodies."""
    if not code or len(code) < 2048:
        return code
    lines = code.split("\n")
    if len(lines) < 50:
        return code

    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _IMPORT_RE.match(line) or line.strip().startswith(("@", "#", "//", "/*")):
            out.append(line)
            i += 1
            continue

        m = _CODE_DEF_RE.match(line)
        if m:
            out.append(line)
            indent = len(m.group("indent"))
            i += 1
            body_count = 0
            while i < n:
                cur = lines[i]
                cur_indent = len(cur) - len(cur.lstrip(" \t")) if cur.strip() else indent + 4
                if cur.strip() and cur_indent <= indent:
                    break
                if body_count < max_body_lines:
                    out.append(cur)
                    body_count += 1
                elif body_count == max_body_lines:
                    out.append(" " * (indent + 4) + "[... implementation body folded ...]")
                    body_count += 1
                i += 1
            continue

        out.append(line)
        i += 1

    res = "\n".join(out)
    return res if len(res) < len(code) else code


# ---------------------------------------------------------------------------
# 5. Line-Anchored Smart AST Skeletonizer (v4.2)
# ---------------------------------------------------------------------------
_MIN_FOLD_BODY_LINES = 15  # functions of 15 lines or fewer are never folded


def smart_skeletonize_code(
    text: str,
    ref: str = "",
    *,
    min_body_lines: int = _MIN_FOLD_BODY_LINES,
    line_prefixes: list[str] | None = None,
    line_nos: list[int] | None = None,
) -> tuple[str, int, int]:
    """Line-anchored AST code skeletonizer with Zero Loss of Signatures/Types.

    When `line_prefixes` / `line_nos` are provided (Hermes `LINE|CONTENT`
    output), `text` must be the unprefixed source and the skeleton re-attaches
    the exact original `LINE|` prefix to every kept line.

    Preserves 100% of:
      - Imports, module constants, global assignments, type definitions
      - Class/Function decorators, full multi-line signatures & type annotations
      - First line of docstrings
      - Short functions/methods (<= min_body_lines)
      - Final return/yield/raise statement of folded functions (for return-type clarity)
    Replaces only the interior implementation body of long functions with an exact
    1-indexed line-range marker so the agent can call read_file(offset=..., limit=...).

    Returns: (skeleton_text, saved_chars, folded_functions_count)
    """
    try:
        tree = ast.parse(text)
    except Exception:
        return text, 0, 0

    lines = text.split("\n")
    total_lines = len(lines)
    use_prefixes = (
        line_prefixes is not None
        and line_nos is not None
        and len(line_prefixes) == total_lines
        and len(line_nos) == total_lines
    )
    # Ranges to fold: (fold_start_1idx, fold_end_1idx, indent_str, func_name)
    fold_ranges: list[tuple[int, int, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not getattr(node, "body", None) or not hasattr(node, "end_lineno"):
            continue
        # Skip folding nested functions inside a parent that is itself folded.
        first_stmt = node.body[0]
        first_fold_stmt = first_stmt
        body_start = first_stmt.lineno  # 1-indexed

        # If the first statement is a docstring, keep the docstring lines and start
        # folding right after it -- the range must stay contiguous or the retrieval
        # coordinates in the marker no longer match the file.
        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(getattr(first_stmt, "value", None), ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            if len(node.body) <= 1:
                continue  # docstring only -> nothing to fold
            doc_end = getattr(first_stmt, "end_lineno", first_stmt.lineno) or first_stmt.lineno
            first_fold_stmt = node.body[1]
            body_start = doc_end + 1

        # A nested decorated def has its decorator on the line ABOVE stmt.lineno,
        # so folding from stmt.lineno would leave "@decorator" above the `pass`
        # -> SyntaxError. Start the fold at the decorator instead.
        decs = getattr(first_fold_stmt, "decorator_list", None)
        if decs:
            body_start = min(body_start, min(getattr(d, "lineno", first_fold_stmt.lineno) for d in decs))

        body_end = node.end_lineno or body_start

        # If the last statement is a short return/raise, keep it (return-type clarity).
        last_stmt = node.body[-1]
        if (
            last_stmt is not first_fold_stmt
            and isinstance(last_stmt, (ast.Return, ast.Raise))
            and hasattr(last_stmt, "lineno")
        ):
            last_span = (getattr(last_stmt, "end_lineno", last_stmt.lineno) or last_stmt.lineno) - last_stmt.lineno + 1
            if last_span <= 2 and last_stmt.lineno > body_start:
                body_end = last_stmt.lineno - 1

        span_lines = body_end - body_start + 1
        if span_lines < min_body_lines:
            continue

        # Indent from the first folded STATEMENT's own line. Reading it from the
        # line after a docstring hit the blank line -> indent 0 -> the `pass`
        # dedented out of the method -> IndentationError.
        stmt_line = lines[first_fold_stmt.lineno - 1] if 1 <= first_fold_stmt.lineno <= total_lines else ""
        indent_len = len(stmt_line) - len(stmt_line.lstrip(" \t"))
        if indent_len > 0:
            indent = stmt_line[:indent_len]
        else:
            indent = " " * (getattr(first_fold_stmt, "col_offset", None)
                            or getattr(node, "col_offset", 0) + 4)
        fold_ranges.append((body_start, body_end, indent, node.name))

    if not fold_ranges:
        return text, 0, 0

    # Sort and drop overlapping ranges (keep the outermost).
    fold_ranges.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    merged_ranges: list[tuple[int, int, str, str]] = []
    last_end = 0
    for s, e, ind, fn in fold_ranges:
        if s <= last_end:
            continue
        merged_ranges.append((s, e, ind, fn))
        last_end = e

    ref_tag = f" {ref}" if ref else ""
    out: list[str] = []
    cur = 1  # 1-indexed line pointer
    for s, e, ind, fn in merged_ranges:
        while cur < s and cur <= total_lines:
            prefix = line_prefixes[cur - 1] if use_prefixes else ""  # type: ignore[index]
            out.append(f"{prefix}{lines[cur - 1]}")
            cur += 1
        # Markers and hints must carry the REAL file line numbers, not the
        # position inside the payload (Hermes `LINE|CONTENT` numbers are the
        # file's own line numbers).
        file_s = line_nos[s - 1] if use_prefixes else s  # type: ignore[index]
        file_e = line_nos[e - 1] if use_prefixes else e  # type: ignore[index]
        omitted = file_e - file_s + 1
        marker_prefix = line_prefixes[s - 1] if use_prefixes else ""  # type: ignore[index]
        # The marker must remain a valid Python statement: a bare comment line
        # would be an IndentationError when it is the only statement of a body.
        marker = (
            f"{marker_prefix}{ind}pass  # [tt:body L{file_s}-L{file_e} ({omitted}L of {fn}){ref_tag} "
            f"-> use read_file(offset={file_s}, limit={omitted}) or trimtok_retrieve]"
        )
        out.append(marker)
        cur = e + 1

    while cur <= total_lines:
        prefix = line_prefixes[cur - 1] if use_prefixes else ""  # type: ignore[index]
        out.append(f"{prefix}{lines[cur - 1]}")
        cur += 1

    new_text = "\n".join(out)
    if use_prefixes:
        orig_len = sum(len(line_prefixes[i]) + len(lines[i]) + 1 for i in range(total_lines)) - 1  # type: ignore[index]
    else:
        orig_len = len(text)
    saved = orig_len - len(new_text)
    if saved <= 0:
        return text, 0, 0
    return new_text, saved, len(merged_ranges)


_TEXT_STRUCT_RE = re.compile(
    r"^ {0,3}(?:#{1,6}\s|```|~~~|\|.*\||[-*_]{3,}\s*$)"  # markdown heading/fence/table/rule
    r"|^(?: {0,2})?[A-Za-z_$][\w.$/-]*:[ \t]*(?:\S|$)"    # yaml / ini key
    r"|^\s{0,3}[A-Za-z][\w.-]*::"                          # rst directive
)
_MIN_TEXT_FOLD_RUN = 4
_MIN_TEXT_FOLD_RATIO = 0.12
_MAX_SHAPE_SAMPLES = 25


def _json_shape(line: str):
    """Structural fingerprint of a JSON record line, or None if it is not one."""
    s = line.strip()
    if not s or s[0] not in "{[":
        return None
    try:
        obj = _json.loads(s)
    except Exception:
        return None
    if isinstance(obj, dict):
        return ("{",) + tuple(sorted(obj))
    if isinstance(obj, list):
        head = tuple(sorted(obj[0])) if obj and isinstance(obj[0], dict) else type(obj[0]).__name__ if obj else ""
        return ("[", len(obj), head)
    return None


def fold_text_structure(
    text: str,
    ref: str = "",
    *,
    line_prefixes: list[str] | None = None,
    line_nos: list[int] | None = None,
) -> tuple[str, int]:
    """Fold long prose runs in markdown/yaml/rst/jsonl, keeping every structural line.

    The Python AST lane is useless for non-code (it returns nothing), so a 21KB
    CHANGELOG used to pass through untouched. This keeps headings, fences, tables
    and key lines with their exact original `LINE|` prefix, and replaces prose with
    a marker naming the real line range so the agent can read_file(offset=...) or
    trimtok_retrieve instead of guessing.

    Records (jsonl / list rows) are never swallowed whole: the first record of
    every distinct key shape survives, so the reader still sees the schema.

    Returns (folded_text, saved_chars); (text, 0) when the fold is not worth it.
    """
    try:
        lines = text.split("\n")
        total = len(lines)
        if total < 40:
            return text, 0
        use_p = (
            line_prefixes is not None
            and line_nos is not None
            and len(line_prefixes) == total
            and len(line_nos) == total
        )
        struct = [bool(_TEXT_STRUCT_RE.match(ln)) for ln in lines]
        out: list[str] = []
        ref_tag = f" {ref}" if ref else ""

        def pfx(k: int) -> str:
            return line_prefixes[k] if use_p else ""  # type: ignore[index]

        def emit_kept(start: int, end: int, keep: set[int]) -> None:
            k = start
            while k < end:
                if k in keep:
                    out.append(f"{pfx(k)}{lines[k]}")
                    k += 1
                    continue
                m = k
                while m < end and m not in keep:
                    m += 1
                s = line_nos[k] if use_p else k + 1  # type: ignore[index]
                e = line_nos[m - 1] if use_p else m  # type: ignore[index]
                out.append(
                    f"{pfx(k)}[tt:prose L{s}-L{e} ({e - s + 1}L){ref_tag} "
                    f"-> read_file(offset={s}, limit={e - s + 1}) or trimtok_retrieve]"
                )
                k = m

        i = 0
        while i < total:
            if struct[i]:
                out.append(f"{pfx(i)}{lines[i]}")
                i += 1
                continue
            j = i
            while j < total and not struct[j]:
                j += 1
            if j - i <= _MIN_TEXT_FOLD_RUN:
                emit_kept(i, j, set(range(i, j)))
            else:
                keep = {i, j - 1}
                shapes: dict[tuple, int] = {}
                for k in range(i, j):
                    sh = _json_shape(lines[k])
                    if sh is not None:
                        shapes.setdefault(sh, k)
                keep |= set(list(shapes.values())[:_MAX_SHAPE_SAMPLES])
                emit_kept(i, j, keep)
            i = j

        saved = sum(len(x) for x in lines) - sum(len(x) for x in out)
        if saved < 200 or saved < int(_MIN_TEXT_FOLD_RATIO * len(text)):
            return text, 0
        return "\n".join(out), saved
    except Exception:
        return text, 0


_SUCCESS_COLLAPSE_CMDS = (
    "npm install", "npm i", "npm ci", "pnpm install", "pnpm i", "pnpm add",
    "yarn install", "yarn add", "docker pull", "git push", "git fetch",
)

def collapse_success_output(cmd: str, text: str, exit_code: int = 0) -> str:
    # Noisy build/install logs that succeeded contain zero intelligence for reasoning
    if exit_code != 0 or not text:
        return text
    clean_cmd = cmd.strip()
    if not any(clean_cmd.startswith(c) for c in _SUCCESS_COLLAPSE_CMDS):
        return text
    # Safety guard: never swallow actual errors even if exit code was spoofed
    lower = text.lower()
    if any(k in lower for k in ("error:", "failed", "traceback", "npm err!", "panic:")):
        return text
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    summary = lines[-1] if lines else "completed"
    if len(lines) > 1 and ("added" in lines[0] or "packages" in lines[0]):
        summary = lines[0]
    return f"[tt:success cmd=\"{clean_cmd}\" {summary}]"

def group_directory_siblings(lines: list[str], threshold: int = 5) -> list[str]:
    # Folding repetitive sibling files in directory trees preserves navigational anchors
    parsed = []
    dir_counts: dict[str, int] = {}
    for l in lines:
        parts = l.strip().split()
        if len(parts) >= 2 and "/" in parts[-1]:
            pfx = l[:l.rfind(parts[-1])]
            fpath = parts[-1]
            pdir = fpath[:fpath.rfind("/") + 1]
            parsed.append((pfx, pdir, fpath, l))
            dir_counts[pdir] = dir_counts.get(pdir, 0) + 1
        else:
            parsed.append(("", "", "", l))

    if not any(c >= threshold for c in dir_counts.values()):
        return lines

    emitted_dirs = set()
    result = []
    for pfx, pdir, fpath, orig in parsed:
        if pdir and dir_counts.get(pdir, 0) >= threshold:
            if pdir not in emitted_dirs:
                emitted_dirs.add(pdir)
                result.append(f"{pfx}{pdir} ({dir_counts[pdir]} files modified) [tt:grouped]")
        else:
            result.append(orig)
    return result

