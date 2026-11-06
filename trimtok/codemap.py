"""Ultra-light codebase mapper: what already exists, so reuse costs no search.

Scans the repository and outputs top-level functions, classes, and exports
grouped one line per directory, bounded by a strict character budget (default 2000 chars).
Stdlib only (git ls-files or os.walk + regex), zero external dependencies.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set

SRC_EXTS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".go", ".rs", ".rb", ".java", ".kt", ".php", ".swift", ".cs"
}

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "out", "vendor",
    "venv", ".venv", "__pycache__", "coverage", ".next", "target",
    "migrations", "versions", "fixtures", "tests", "__tests__", "spec",
    "site-packages", ".cache", "archive-v0", "bower_components", ".tox",
}

SHARED_KEYWORDS = re.compile(
    r"(util|helper|lib|common|shared|service|component|hook|core|model|schema|api)",
    re.IGNORECASE
)

PATTERNS = {
    "py": [
        re.compile(r"^(?:async\s+)?def\s+([A-Za-z]\w*)", re.MULTILINE),
        re.compile(r"^class\s+([A-Za-z]\w*)", re.MULTILINE),
    ],
    "js": [
        re.compile(
            r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
            re.MULTILINE
        ),
    ],
    "go": [
        re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)", re.MULTILINE),
        re.compile(r"^type\s+([A-Z]\w*)", re.MULTILINE),
    ],
    "rs": [
        re.compile(r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type)\s+(\w+)", re.MULTILINE),
    ],
    "jvm": [
        re.compile(r"^\s*(?:public\s+)?(?:static\s+)?(?:final\s+)?(?:class|interface|enum|record)\s+(\w+)", re.MULTILINE),
    ],
    "rb": [
        re.compile(r"^\s*(?:def|class|module)\s+([A-Za-z][\w.]*)", re.MULTILINE),
    ],
}

LANG_MAP = {
    ".py": "py",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js",
    ".go": "go",
    ".rs": "rs",
    ".java": "jvm", ".kt": "jvm", ".swift": "jvm", ".cs": "jvm", ".php": "jvm",
    ".rb": "rb",
}


def _list_files(root: Path, max_files: int) -> list[str]:
    """List source files in root using git ls-files if available, fallback to os.walk."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            lines = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
            if lines:
                return lines[:max_files * 5]
    except Exception:
        pass

    out: list[str] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        rel_dir = os.path.relpath(dirpath, root_str).replace("\\", "/")
        parts = rel_dir.split("/")
        if any(p in SKIP_DIRS or p.startswith(".") for p in parts if p and p != "."):
            dirnames.clear()
            continue

        depth = len(parts) if rel_dir != "." else 0
        if depth > 6:
            dirnames.clear()
            continue

        for fn in filenames:
            if fn.startswith("."):
                continue
            rel_file = (f"{rel_dir}/{fn}" if rel_dir != "." else fn).replace("\\", "/")
            out.append(rel_file)
            if len(out) >= max_files * 5:
                return out
    return out


def _extract_names(text: str, ext: str) -> list[str]:
    """Extract symbol names from text based on extension."""
    lang = LANG_MAP.get(ext)
    if not lang:
        return []
    patterns = PATTERNS.get(lang, [])
    names: list[str] = []
    seen: Set[str] = set()
    for pat in patterns:
        for match in pat.finditer(text):
            name = match.group(1).strip()
            if name and not name.startswith("_") and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _extract_import_stems(text: str, ext: str) -> set[str]:
    """Extract imported module names / stems from file content without external dependencies."""
    stems: set[str] = set()
    lang = LANG_MAP.get(ext)
    if lang == "py":
        for m in re.finditer(r"(?:from\s+[\.]*([a-zA-Z0-9_\.]+)|import\s+([a-zA-Z0-9_\.,\s]+))", text):
            matched = m.group(1) or m.group(2)
            if matched:
                for chunk in re.split(r"[,\s]+", matched):
                    for sub in chunk.split("."):
                        clean = sub.strip().lower()
                        if clean and not clean.startswith("_"):
                            stems.add(clean)
    elif lang == "js":
        for m in re.finditer(r"(?:from|require\()\s*['\"]([^'\"]+)['\"]", text):
            path_str = m.group(1).rstrip("/")
            for part in path_str.split("/"):
                sub = os.path.splitext(part)[0].strip().lower()
                if sub and sub not in (".", ".."):
                    stems.add(sub)
    elif lang == "go":
        # Only real import paths: a bare `"..."` anywhere would also catch
        # every string literal in the file (fmt.Println("hello")).
        for block in re.finditer(r"^import\s*\(([^)]*)\)", text, re.MULTILINE):
            for m in re.finditer(r'"([^"]+)"', block.group(1)):
                for part in m.group(1).rstrip("/").split("/"):
                    if part:
                        stems.add(part.lower())
        for m in re.finditer(r'^import\s+(?:\w+\s+)?"([^"]+)"', text, re.MULTILINE):
            for part in m.group(1).rstrip("/").split("/"):
                if part:
                    stems.add(part.lower())
    elif lang == "rs":
        for m in re.finditer(r"use\s+([a-zA-Z0-9_:]+)", text):
            for part in m.group(1).split("::"):
                part_clean = part.strip().lower()
                if part_clean:
                    stems.add(part_clean)
    return stems


def build_codebase_map(
    root: str | Path | None = None,
    budget_chars: int = 2000,
    max_files: int = 4000,
    focus_file: str | None = None,
) -> str:
    """Build a compact, single-line-per-directory summary of code symbols."""
    target_root = Path(root) if root else Path.cwd()
    if not target_root.is_dir():
        return ""

    raw_files = _list_files(target_root, max_files)
    valid_files: list[str] = []
    for f in raw_files:
        ext = os.path.splitext(f)[1].lower()
        if ext not in SRC_EXTS:
            continue
        parts = f.split("/")
        if any(p in SKIP_DIRS for p in parts):
            continue
        base = os.path.basename(f)
        if (
            base.startswith(("test_", "test.", "spec_", "spec."))
            or "_test." in base
            or ".test." in base
            or ".spec." in base
            or base in ("test.py", "spec.py")
        ):
            continue
        valid_files.append(f)

    focus_clean = ""
    focus_dir = ""
    focus_imports: set[str] = set()
    if focus_file:
        fc = str(focus_file).replace("\\", "/").strip()
        if fc.startswith("./"):
            fc = fc[2:]
        fp = target_root / fc
        if fp.is_file():
            focus_clean = fc
            focus_dir = os.path.dirname(fc)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    fcontent = fh.read(65536)
                focus_imports = _extract_import_stems(fcontent, os.path.splitext(fc)[1].lower())
            except Exception:
                pass

    def file_relevance(f: str) -> tuple[int, int, str]:
        if not focus_clean:
            is_shared = 0 if SHARED_KEYWORDS.search(f) else 1
            return (is_shared, 0, f)
        f_norm = f.replace("\\", "/").strip()
        f_stem = Path(f_norm).stem.lower()
        if f_norm == focus_clean or f_norm.endswith("/" + focus_clean):
            return (0, 0, f)
        # Match on the file stem or its immediate package only: matching any
        # path component would rank every file under a top-level dir like src/.
        if f_stem in focus_imports or Path(f_norm).parent.name.lower() in focus_imports:
            return (1, 0, f)
        if focus_dir and os.path.dirname(f_norm) == focus_dir:
            return (2, 0, f)
        is_shared = 0 if SHARED_KEYWORDS.search(f) else 1
        return (3, is_shared, f)

    valid_files.sort(key=file_relevance)
    valid_files = valid_files[:max_files]

    dirs: Dict[str, list[tuple[str, list[str]]]] = {}
    dir_ranks: Dict[str, int] = {}
    for f in valid_files:
        full_path = target_root / f
        try:
            if not full_path.is_file():
                continue
            with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(65536)
        except Exception:
            continue

        ext = os.path.splitext(f)[1].lower()
        names = _extract_names(content, ext)
        if not names:
            continue

        dir_name = os.path.dirname(f)
        dir_key = dir_name if dir_name else "."
        dirs.setdefault(dir_key, []).append((f, names))
        rank = file_relevance(f)[0]
        if dir_key not in dir_ranks or rank < dir_ranks[dir_key]:
            dir_ranks[dir_key] = rank

    lines: list[str] = []
    used_len = 0
    skipped_count = 0

    sorted_dir_keys = sorted(dirs.keys(), key=lambda d: (dir_ranks.get(d, 99), d))
    for dir_name in sorted_dir_keys:
        per_file_symbols = dirs[dir_name]
        dir_symbols: list[str] = []
        seen: Set[str] = set()
        for f, file_syms in per_file_symbols:
            # The focus file keeps a wider slice of its own API; everything
            # else collapses to one symbol per file once a directory is busy.
            if focus_clean and (f == focus_clean or f.endswith("/" + focus_clean)):
                limit = 8
            else:
                limit = 1 if len(per_file_symbols) > 8 else 3
            for s in file_syms[:limit]:
                if s not in seen:
                    seen.add(s)
                    dir_symbols.append(s)

        if not dir_symbols:
            continue

        dir_prefix = f"{dir_name}/: " if dir_name != "." else "./: "
        remaining_budget = budget_chars - (used_len + len(dir_prefix) + 40)
        if remaining_budget <= 0 and lines:
            skipped_count += 1
            continue

        # Truncate symbols to fit line in remaining budget if needed
        while len(", ".join(dir_symbols)) > max(20, remaining_budget) and len(dir_symbols) > 1:
            dir_symbols.pop()

        sym_str = ", ".join(dir_symbols[:30])
        line = f"{dir_prefix}{sym_str}"
        if used_len + len(line) + 1 > budget_chars and lines:
            skipped_count += 1
            continue

        lines.append(line)
        used_len += len(line) + 1

    if not lines:
        return ""

    head = f"Codebase map ({len(lines)} directories"
    if skipped_count > 0:
        head += f", {skipped_count} omitted for budget"
    head += "):"

    full_text = f"{head}\n" + "\n".join(lines)
    if len(full_text) > budget_chars:
        # Fallback safeguard
        trimmed = full_text[:budget_chars]
        full_text = trimmed.rsplit("\n", 1)[0] if "\n" in trimmed else ""
    return full_text
