"""Stub-corruption & write-back integrity guard for Trimtok.

Prevents compressed `[tt:...]` / `[@#c...]` CCR stubs from being written back
into source, documentation, or skill files (the failure mode where an agent
reads a folded view and persists the placeholder marker onto disk).

100% standalone and offline: inspects text strings, tool-call write arguments,
files/directories on disk, or staged git diffs while ignoring backticked docs.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

# Inline code spans (`...`) document marker syntax without being live stubs.
_INLINE_BACKTICK_RE = re.compile(r"`[^`\n]+`")

# Real persisted Trimtok stub markers carry concrete line ranges, counts, or
# Base62/hex CCR references (never f-string templates like `L{s}-L{e}`).
_STUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "tt_prose_stub",
        re.compile(r"\[tt:prose\s+L\d+-L\d+\s+\(\d+L\)(?:\s+#[0-9A-Za-z]+)?[^\]]*\]"),
    ),
    (
        "tt_body_stub",
        re.compile(r"\[tt:body\s+L\d+-L\d+\s+\(\d+L\s+of\s+[^)\]]+\)(?:\s+#[0-9A-Za-z]+)?[^\]]*\]"),
    ),
    (
        "tt_folded_stub",
        re.compile(r"\[tt:folded\s+(?:L\d+-L\d+\s+\(\d+L\)|\d+\s+chars\s+#[0-9A-Za-z]+)[^\]]*\]"),
    ),
    (
        "tt_file_stub",
        re.compile(r"\[tt:(?:file_delta|file_large|file_unchanged|lockfile)\b[^\]]*(?:#[0-9A-Za-z]+|path=)[^\]]*\]"),
    ),
    (
        "tt_bounded_stub",
        re.compile(r"\[tt:(?:pointer_large|timeout_bounded)\b[^\]]*#[0-9A-Za-z]+[^\]]*\]"),
    ),
    (
        "tt_stashed_stub",
        re.compile(r"\[tt:stashed\s+\d+\s+chars\b[^\]]*\]"),
    ),
    (
        "tt_ellipsis_stub",
        re.compile(
            r"\[\.\.\.\s*(?:implementation body folded|L\d+-L\d+\s+\(\d+\s+unchanged lines in #[\w]+\)|[\d,]+\s+lines in CCR)[^\]]*\]"
        ),
    ),
    (
        "tt_alias_marker",
        re.compile(r"\[@#c[0-9A-Za-z]+(?:x\d+)?\]"),
    ),
)

_WRITE_CONTENT_KEYS = (
    "content",
    "new_string",
    "replacement",
    "Replacement",
    "Content",
    "patch",
    "diff",
    "body",
    "text",
    "code",
)

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".pytest_cache"})
_TEXT_EXTS = frozenset({
    ".md", ".txt", ".rst", ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".cc", ".rb",
    ".php", ".cs", ".swift", ".scala", ".yaml", ".yml", ".toml", ".json",
    ".sh", ".ps1", ".sql", ".html", ".css",
})


def find_stub_markers(text: str) -> list[dict[str, Any]]:
    """Return concrete Trimtok stub markers found outside inline backticks."""
    if not text or not isinstance(text, str):
        return []
    if "[tt:" not in text and "[@#" not in text and "[..." not in text:
        return []

    findings: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if "[tt:" not in line and "[@#" not in line and "[..." not in line:
            continue
        cleaned = _INLINE_BACKTICK_RE.sub("", line)
        for kind, rx in _STUB_PATTERNS:
            m = rx.search(cleaned)
            if m:
                findings.append({
                    "line": lineno,
                    "kind": kind,
                    "marker": m.group(0)[:140],
                })
                break
    return findings


def has_persisted_stubs(text: str) -> bool:
    """True if text contains at least one concrete persisted Trimtok stub."""
    return bool(find_stub_markers(text))


def check_write_args(tool_name: str, args: Any) -> dict[str, Any] | None:
    """Inspect a mutating tool call's arguments for Trimtok stubs.

    Returns a block payload dict when concrete stubs are detected, else None.
    """
    try:
        from .policy import _EDIT_TOOLS
    except ImportError:
        from policy import _EDIT_TOOLS  # type: ignore

    name = str(tool_name or "").strip()
    if name not in _EDIT_TOOLS and name.lower() not in _EDIT_TOOLS:
        return None
    if not isinstance(args, dict):
        return None

    target_path = str(
        args.get("path")
        or args.get("Path")
        or args.get("file_path")
        or args.get("target_file")
        or args.get("TargetFile")
        or args.get("name")
        or ""
    ).strip()

    def _inspect_mapping(m: dict[str, Any]) -> dict[str, Any] | None:
        sub_path = str(
            m.get("path")
            or m.get("Path")
            or m.get("file_path")
            or m.get("target_file")
            or m.get("TargetFile")
            or m.get("name")
            or target_path
        ).strip()
        norm_sub = sub_path.replace("\\", "/").lower()
        if "/tests/" in norm_sub or norm_sub.startswith("tests/"):
            return None
        for key in _WRITE_CONTENT_KEYS:
            val = m.get(key)
            if not isinstance(val, str) or not val:
                continue
            hits = find_stub_markers(val)
            if hits:
                return {
                    "blocked": True,
                    "reason": "stub_write_back_detected",
                    "tool": name,
                    "field": key,
                    "path": sub_path,
                    "hits": hits[:10],
                    "message": (
                        f"[trimtok guard] Blocked {name} write containing compressed Trimtok stub "
                        f"({hits[0]['marker']}). Call `trimtok_retrieve` or `read_file` with explicit "
                        f"offset/limit to recover exact lines before writing."
                    ),
                }
        return None

    direct = _inspect_mapping(args)
    if direct is not None:
        return direct
    ops = args.get("operations")
    if isinstance(ops, list):
        for op in ops:
            if isinstance(op, dict):
                res = _inspect_mapping(op)
                if res is not None:
                    return res
    return None


def scan_paths(paths: list[str | Path] | None = None, *, staged: bool = False) -> dict[str, Any]:
    """Scan workspace paths or staged git additions for persisted stub markers."""
    findings: list[dict[str, Any]] = []
    files_scanned = 0

    if staged:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--no-color"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        cur_file = "<staged>"
        cur_line = 0
        for raw in (proc.stdout or "").splitlines():
            if raw.startswith("+++ b/"):
                cur_file = raw[6:].strip()
                continue
            if raw.startswith("@@ "):
                m = re.search(r"\+(\d+)", raw)
                cur_line = int(m.group(1)) if m else 0
                continue
            if raw.startswith("+") and not raw.startswith("+++"):
                added = raw[1:]
                hits = find_stub_markers(added)
                for h in hits:
                    findings.append({
                        "file": cur_file,
                        "line": cur_line or h["line"],
                        "kind": h["kind"],
                        "marker": h["marker"],
                    })
                cur_line += 1
        return {
            "mode": "staged",
            "files_scanned": files_scanned,
            "corrupted_files": len({f["file"] for f in findings}),
            "findings": findings,
            "clean": len(findings) == 0,
        }

    targets = [Path(p).expanduser() for p in (paths or ["."])]
    seen_files: set[str] = set()
    for root in targets:
        if not root.exists():
            continue
        candidates: list[Path] = []
        if root.is_file():
            candidates = [root]
        else:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
                for fn in filenames:
                    fp = Path(dirpath) / fn
                    if fp.suffix.lower() in _TEXT_EXTS or fn == "SKILL.md":
                        candidates.append(fp)
        for fp in candidates:
            try:
                key = str(fp.resolve())
            except Exception:
                key = str(fp)
            if key in seen_files:
                continue
            seen_files.add(key)
            # Skip test files exercising stub markers intentionally
            if any(part == "tests" for part in fp.parts):
                continue
            files_scanned += 1
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for h in find_stub_markers(text):
                findings.append({
                    "file": str(fp),
                    "line": h["line"],
                    "kind": h["kind"],
                    "marker": h["marker"],
                })

    return {
        "mode": "paths",
        "files_scanned": files_scanned,
        "corrupted_files": len({f["file"] for f in findings}),
        "findings": findings,
        "clean": len(findings) == 0,
    }
