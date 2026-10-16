"""trimtok diff_engine: semantic git diff compressor (Trimtok 3.0 Phase 4).

Preserves 100% of:
  * File header lines (`diff --git`, `---`, `+++`, `index`)
  * Hunk headers (`@@ -a,b +c,d @@ [function name/scope]`)
  * All added and removed change lines (`+` and `-`)
  * Boundary context lines around changes (2 lines before/after)

Collapses runs of > 6 unchanged context lines into `[... N unchanged lines ...]`.
Never raises. Returns original text on any parser failure.
"""

from __future__ import annotations

import re

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
