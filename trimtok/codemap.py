"""Ultra-light codebase mapper: what already exists, so reuse costs no search.

Scans the repository and outputs top-level functions, classes, and exports
grouped one line per file, bounded by a strict character budget (default 2000 chars).
Stdlib only (git ls-files or os.walk + regex), zero external dependencies.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Set

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


def _is_source_candidate(rel_file: str) -> bool:
    ext = os.path.splitext(rel_file)[1].lower()
    if ext not in SRC_EXTS:
        return False
    parts = rel_file.split("/")
    if any(p in SKIP_DIRS for p in parts):
        return False
    base = os.path.basename(rel_file)
    if (
        base.startswith(("test_", "test.", "spec_", "spec."))
        or "_test." in base
        or ".test." in base
        or ".spec." in base
        or base in ("test.py", "spec.py")
    ):
        return False
    return True


def _list_files(root: Path, max_files: int) -> list[str]:
    """List source files in root using git ls-files if available, fallback to os.walk."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", "ls-files"],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            out: list[str] = []
            for raw in proc.stdout.splitlines():
                rel = raw.strip().replace("\\", "/")
                if rel and _is_source_candidate(rel):
                    out.append(rel)
                    if len(out) >= max_files:
                        break
            if out:
                return out
    except Exception:
        pass

    out = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        # Prune skipped/hidden directories BEFORE descending so node_modules/.git are never opened
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root_str).replace("\\", "/")
        depth = 0 if rel_dir == "." else rel_dir.count("/") + 1
        if depth > 6:
            dirnames.clear()
            continue

        for fn in filenames:
            if fn.startswith("."):
                continue
            rel_file = f"{rel_dir}/{fn}" if rel_dir != "." else fn
            if _is_source_candidate(rel_file):
                out.append(rel_file)
                if len(out) >= max_files:
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
        for m in re.finditer(
            r"^[ \t]*(?:from[ \t]+\.*([A-Za-z0-9_.]+)[ \t]+import|import[ \t]+([A-Za-z0-9_., \t]+))",
            text,
            re.MULTILINE,
        ):
            matched = m.group(1) or m.group(2)
            if matched:
                for chunk in re.split(r"[,\s]+", matched):
                    if chunk == "as":
                        break
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
        for m in re.finditer(r"^[ \t]*(?:pub[ \t]+)?use[ \t]+([A-Za-z0-9_:]+)", text, re.MULTILINE):
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
    """Build a compact, single-line-per-file summary of code symbols."""
    target_root = (Path(root) if root else Path.cwd()).resolve()
    if not target_root.is_dir():
        return ""

    valid_files = _list_files(target_root, max_files)

    focus_clean = ""
    focus_dir = ""
    focus_imports: set[str] = set()
    if focus_file:
        fc_raw = str(focus_file).strip()
        fc_path = Path(fc_raw)
        fp = fc_path.resolve() if fc_path.is_absolute() else (target_root / fc_raw).resolve()
        if fp.is_file():
            try:
                rel_focus = os.path.relpath(str(fp), str(target_root)).replace("\\", "/")
                if not rel_focus.startswith(".."):
                    focus_clean = rel_focus
            except Exception:
                focus_clean = fc_raw.replace("\\", "/").lstrip("./")
            if focus_clean:
                focus_dir = os.path.dirname(focus_clean)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        fcontent = fh.read(65536)
                    focus_imports = _extract_import_stems(fcontent, os.path.splitext(focus_clean)[1].lower())
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
        if f_stem in focus_imports or Path(f_norm).parent.name.lower() in focus_imports:
            return (1, 0, f)
        if focus_dir and os.path.dirname(f_norm) == focus_dir:
            return (2, 0, f)
        is_shared = 0 if SHARED_KEYWORDS.search(f) else 1
        return (3, is_shared, f)

    ranked_files = [(file_relevance(f), f) for f in valid_files]
    ranked_files.sort(key=lambda item: item[0])
    ranked_files = ranked_files[:max_files]

    dirs: Dict[str, list[tuple[str, list[str]]]] = {}
    dir_ranks: Dict[str, int] = {}
    est_chars = 0
    # Stop scanning disk once we have comfortable headroom over budget_chars
    scan_cap = max(budget_chars * 3, 6000)
    unscanned = 0
    for idx, (rel_key, f) in enumerate(ranked_files):
        if est_chars >= scan_cap and rel_key[0] >= 2:
            unscanned = len(ranked_files) - idx
            break
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
        rank = rel_key[0]
        if dir_key not in dir_ranks or rank < dir_ranks[dir_key]:
            dir_ranks[dir_key] = rank
        est_chars += len(f) + 40

    # Reserve space for header ("Codebase map (N files, M omitted for budget):\n")
    header_reserve = 64
    body_budget = max(80, budget_chars - header_reserve)

    lines: list[str] = []
    used_len = 0
    skipped_count = unscanned

    sorted_dir_keys = sorted(dirs.keys(), key=lambda d: (dir_ranks.get(d, 99), d))
    for dir_name in sorted_dir_keys:
        for f, file_syms in dirs[dir_name]:
            limit = 8 if (focus_clean and (f == focus_clean or f.endswith("/" + focus_clean))) else 3
            syms = file_syms[:limit]
            if not syms:
                continue

            prefix = f"{f}: "
            remaining_budget = body_budget - (used_len + len(prefix) + 4)
            if remaining_budget <= 0 and lines:
                skipped_count += 1
                continue

            while len(", ".join(syms)) > max(10, remaining_budget) and len(syms) > 1:
                syms.pop()

            line = f"{prefix}{', '.join(syms)}"
            if used_len + len(line) + 1 > body_budget and lines:
                skipped_count += 1
                continue

            lines.append(line)
            used_len += len(line) + 1

    if not lines:
        return ""

    def _build_full(kept: list[str], skipped: int) -> str:
        head = f"Codebase map ({len(kept)} files"
        if skipped > 0:
            head += f", {skipped} omitted for budget"
        head += "):"
        return f"{head}\n" + "\n".join(kept)

    full_text = _build_full(lines, skipped_count)
    while len(full_text) > budget_chars and len(lines) > 1:
        lines.pop()
        skipped_count += 1
        full_text = _build_full(lines, skipped_count)
    if len(full_text) > budget_chars:
        return full_text[:budget_chars]
    return full_text
