"""trimtok pruners: semantic diff compression, stack-trace pruning, scoped search grouping, and AST code skeletonization."""

from __future__ import annotations

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
