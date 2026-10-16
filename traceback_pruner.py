"""trimtok traceback_pruner: smart stack-trace frame compressor (Trimtok 3.0 Phase 4).

Collapses consecutive framework/stdlib middle frames into `[... N framework frames omitted ...]`,
while preserving 100% of:
  * First frame (Entrypoint)
  * ALL user/project code frames
  * Crash site frame (last frame)
  * Exception class and error message lines

Never raises. Returns original text on any refusal or parser error.
"""

from __future__ import annotations

import re

_PY_TB_START = re.compile(r"^\s*Traceback \(most recent call last\):")
_PY_FRAME_RE = re.compile(r"^  File \"([^\"]+)\", line (\d+), in (.*)$")
_JS_FRAME_RE = re.compile(r"^\s+at\s+(?:(?:new\s+)?([^\s(]+)\s+\()?(.*?)(?::(\d+):(\d+))?\)?$")

_FRAMEWORK_PATH_RE = re.compile(
    r"(?i)(?:site-packages|dist-packages|node_modules|<frozen|lib[/\\]python\d|"
    r"unittest[/\\]case\.py|_pytest[/\\]|pluggy[/\\]|starlette[/\\]|"
    r"fastapi[/\\]|click[/\\]|tornado[/\\]|flask[/\\]|urllib3[/\\]|requests[/\\])"
)

MIN_FRAMEWORK_RUN = 2  # collapse runs of >= this many framework frames


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
        # Collect frames: each frame is a 2-line pair (header + code line)
        frames: list[tuple[str, str, bool]] = []  # (hdr, code, is_framework)
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
            # Too short to prune safely
            out.append(tb_head)
            for hdr, code, _ in frames:
                out.append(hdr)
                if code:
                    out.append(code)
            continue

        # Prune middle framework frames
        # Rule: Keep frame 0 (entrypoint) and frame -1 (crash site) always
        kept_blocks: list[tuple[str, str] | str] = []
        kept_blocks.append((frames[0][0], frames[0][1]))

        fw_run = 0
        j = 1
        m_frames = len(frames) - 1  # end before crash site
        while j < m_frames:
            hdr, code, is_fw = frames[j]
            if is_fw:
                fw_run += 1
                j += 1
                continue
            # End of framework run
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

        # Crash site frame
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

        # Collect contiguous 'at ...' frames
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

        # Keep first frame
        out.append(frames[0][0])
        # Middle frames
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
        # Last frame
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
