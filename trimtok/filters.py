"""Command-specific terminal output filters for trimtok.

Each filter parses a known command's output into a compact summary.
Returns (summary, stats) or None if the command/output doesn't match.
"""
import re
import os


def _git_log_filter(output: str) -> str | None:
    """git log → commit count, authors, date range."""
    commits = re.findall(r"^commit [0-9a-f]{7,}", output, re.M)
    if len(commits) < 3:
        return None
    authors = set(re.findall(r"^Author:\s*(.+)", output, re.M))
    dates = re.findall(r"^Date:\s*(.+)", output, re.M)
    lines = [
        f"[tt:git_log] {len(commits)} commits, {len(authors)} authors",
    ]
    if authors:
        lines.append(f"  authors: {', '.join(sorted(authors)[:5])}")
    if dates:
        lines.append(f"  range: {dates[-1].strip()} .. {dates[0].strip()}")
    return "\n".join(lines)


def _git_status_filter(output: str) -> str | None:
    """git status → modified/added/deleted counts."""
    modified = re.findall(r"^\s*modified:\s*(.+)", output, re.M)
    added = re.findall(r"^\s*new file:\s*(.+)", output, re.M)
    deleted = re.findall(r"^\s*deleted:\s*(.+)", output, re.M)
    untracked = re.findall(r"^\t(.+)$", output, re.M)
    total = len(modified) + len(added) + len(deleted) + len(untracked)
    if total < 3:
        return None
    parts = []
    if modified:
        parts.append(f"{len(modified)} modified")
    if added:
        parts.append(f"{len(added)} added")
    if deleted:
        parts.append(f"{len(deleted)} deleted")
    if untracked:
        parts.append(f"{len(untracked)} untracked")
    return f"[tt:git_status] {', '.join(parts)}"


def _ls_filter(output: str) -> str | None:
    """ls -la → file/dir counts and total size."""
    lines = output.strip().splitlines()
    if len(lines) < 5:
        return None
    dirs = sum(1 for ln in lines if ln.startswith("d"))
    files = sum(1 for ln in lines if ln.startswith("-"))
    total_size = 0
    for ln in lines:
        parts = ln.split()
        if len(parts) >= 5 and parts[4].isdigit():
            total_size += int(parts[4])
    if files + dirs < 3:
        return None
    size_str = f"{total_size / 1024:.1f}KB" if total_size < 1024 * 1024 else f"{total_size / (1024*1024):.1f}MB"
    return f"[tt:ls] {files} files, {dirs} dirs, {size_str} total"


def _npm_filter(output: str) -> str | None:
    """npm install/list → summary."""
    added = re.findall(r"added \d+ packages", output)
    if not added and "up to date" not in output.lower():
        return None
    lines = output.strip().splitlines()
    if len(lines) < 5:
        return None
    pkgs = re.findall(r"^\s*[+├└]--\s*(.+)", output, re.M)
    if pkgs and len(pkgs) >= 3:
        return f"[tt:npm] {len(pkgs)} packages listed"
    if added:
        return f"[tt:npm] {added[0]}"
    if "up to date" in output.lower():
        return "[tt:npm] up to date"
    return None


def _pip_filter(output: str) -> str | None:
    """pip install/list → summary."""
    installed = re.findall(r"Successfully installed (.+)", output)
    if installed:
        pkgs = installed[0].split()
        return f"[tt:pip] installed {len(pkgs)} packages: {', '.join(pkgs[:5])}"
    reqs = re.findall(r"Requirement already satisfied: (.+)", output)
    if len(reqs) >= 3:
        return f"[tt:pip] {len(reqs)} requirements already satisfied"
    return None


def _find_filter(output: str) -> str | None:
    """find → file count by extension."""
    lines = output.strip().splitlines()
    if len(lines) < 5:
        return None
    exts: dict = {}
    for ln in lines:
        ext = os.path.splitext(ln.strip())[1].lower() or "(no ext)"
        exts[ext] = exts.get(ext, 0) + 1
    if len(exts) < 2:
        return None
    parts = [f"{ext}: {n}" for ext, n in sorted(exts.items(), key=lambda x: -x[1])[:5]]
    return f"[tt:find] {len(lines)} files — {', '.join(parts)}"
_FILTERS = [
    (re.compile(r"\bgit\s+log\b", re.I), _git_log_filter),
    (re.compile(r"\bgit\s+status\b", re.I), _git_status_filter),
    (re.compile(r"\bls\s+-", re.I), _ls_filter),
    (re.compile(r"\bnpm\b", re.I), _npm_filter),
    (re.compile(r"\bpip\b", re.I), _pip_filter),
    (re.compile(r"\bfind\b", re.I), _find_filter),
]


def try_command_filter(command: str, output: str) -> str | None:
    """Try all registered filters. Returns summary or None."""
    for pattern, fn in _FILTERS:
        if pattern.search(command or ""):
            result = fn(output)
            if result is not None:
                return result
    return None