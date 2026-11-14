"""Command-specific terminal output filters for trimtok.

Each filter parses a known command's output into a compact summary.
Returns summary string or None if the command/output doesn't match.
All operations are strictly fail-open and never raise.
"""
import os
import re

# Pre-compiled regexes for maximum throughput
_RE_GIT_LOG_COMMITS = re.compile(r"^commit [0-9a-f]{7,}", re.M)
_RE_GIT_LOG_AUTHORS = re.compile(r"^Author:\s*(.+)", re.M)
_RE_GIT_LOG_DATES = re.compile(r"^Date:\s*(.+)", re.M)

_RE_GIT_STATUS_MOD = re.compile(r"^\s*modified:\s*(.+)", re.M)
_RE_GIT_STATUS_ADD = re.compile(r"^\s*new file:\s*(.+)", re.M)
_RE_GIT_STATUS_DEL = re.compile(r"^\s*deleted:\s*(.+)", re.M)
_RE_GIT_STATUS_UNT = re.compile(r"^	(.+)$", re.M)

_RE_NPM_ADDED = re.compile(r"added \d+ packages")
_RE_NPM_TREE = re.compile(r"^\s*[+`├└]--\s*(.+)", re.M)

_RE_PIP_INSTALLED = re.compile(r"Successfully installed (.+)")
_RE_PIP_SATISFIED = re.compile(r"Requirement already satisfied: (.+)")


def _git_log_filter(output: str) -> str | None:
    """git log → commit count, authors, date range."""
    commits = _RE_GIT_LOG_COMMITS.findall(output)
    if len(commits) < 3:
        return None
    authors = set(_RE_GIT_LOG_AUTHORS.findall(output))
    dates = _RE_GIT_LOG_DATES.findall(output)
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
    modified = _RE_GIT_STATUS_MOD.findall(output)
    added = _RE_GIT_STATUS_ADD.findall(output)
    deleted = _RE_GIT_STATUS_DEL.findall(output)
    untracked = _RE_GIT_STATUS_UNT.findall(output)
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


_RE_LS_LINE = re.compile(r"^[d\-lcbps][rwx\-sStT]{9}[+@.]?\s+")


def _ls_filter(output: str) -> str | None:
    """ls -la → file/dir counts and total size."""
    lines = [ln for ln in output.strip().splitlines() if ln.strip()]
    if len(lines) < 5:
        return None
    entry_lines = [ln for ln in lines if _RE_LS_LINE.match(ln)]
    if len(entry_lines) < 3:
        return None
    # Guard against mixed output: most non-header lines must be ls entries.
    body_count = len(lines) - (1 if lines[0].lstrip().startswith("total ") else 0)
    if body_count <= 0 or len(entry_lines) * 10 < body_count * 6:
        return None
    dirs = sum(1 for ln in entry_lines if ln.startswith("d"))
    files = sum(1 for ln in entry_lines if ln.startswith("-"))
    total_size = 0
    for ln in entry_lines:
        parts = ln.split()
        if len(parts) >= 5 and parts[4].isdigit():
            total_size += int(parts[4])
    if files + dirs < 3:
        return None
    size_str = f"{total_size / 1024:.1f}KB" if total_size < 1024 * 1024 else f"{total_size / (1024*1024):.1f}MB"
    return f"[tt:ls] {files} files, {dirs} dirs, {size_str} total"


def _npm_filter(output: str) -> str | None:
    """npm install/list → summary."""
    lines = output.strip().splitlines()
    if len(lines) < 4:
        return None
    pkgs = _RE_NPM_TREE.findall(output)
    if pkgs and len(pkgs) >= 3:
        return f"[tt:npm] {len(pkgs)} packages listed"
    added = _RE_NPM_ADDED.findall(output)
    if added:
        return f"[tt:npm] {added[0]}"
    if "up to date" in output.lower():
        return "[tt:npm] up to date"
    return None


def _pip_filter(output: str) -> str | None:
    """pip install/list → summary."""
    installed = _RE_PIP_INSTALLED.findall(output)
    if installed:
        pkgs = installed[0].split()
        return f"[tt:pip] installed {len(pkgs)} packages: {', '.join(pkgs[:5])}"
    reqs = _RE_PIP_SATISFIED.findall(output)
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


_RE_DOCKER_PS_HDR = re.compile(r"CONTAINER ID\s+IMAGE", re.I)

def _docker_ps_filter(output: str) -> str | None:
    """docker ps → container count, running images, names."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if len(lines) < 2 or not _RE_DOCKER_PS_HDR.search(lines[0]):
        return None
    rows = lines[1:]
    if len(rows) < 2:
        return None
    images, names = [], []
    for r in rows:
        parts = r.split()
        if len(parts) >= 2:
            images.append(parts[1])
        if len(parts) >= 7:
            names.append(parts[-1])
    uniq_images = sorted(set(images))[:4]
    name_snippet = f", names: {', '.join(names[:4])}" if names else ""
    return f"[tt:docker_ps] {len(rows)} containers ({', '.join(uniq_images)}{name_snippet})"


_RE_COMPOUND = re.compile(r"(?:&&|\|\||<<|[;|\n`]|[$][(]|\s&\s)")
_RE_LEADING_ENV = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+)\s+|env\s+)*"
)

_FILTERS = [
    (re.compile(r"^(?:\S*/)?git\s+log\b", re.I), _git_log_filter),
    (re.compile(r"^(?:\S*/)?git\s+status\b", re.I), _git_status_filter),
    (re.compile(r"^(?:\S*/)?ls\s+-", re.I), _ls_filter),
    (re.compile(r"^(?:\S*/)?npm\b", re.I), _npm_filter),
    (re.compile(r"^(?:\S*/)?pip(?:3)?\b", re.I), _pip_filter),
    (re.compile(r"^(?:\S*/)?find\b", re.I), _find_filter),
    (re.compile(r"^(?:\S*/)?docker\s+(?:container\s+)?(?:ps|ls)\b", re.I), _docker_ps_filter),
]


def try_command_filter(command: str, output: str) -> str | None:
    """Try all registered filters. Returns summary or None. Never raises."""
    if not command or not output or not isinstance(output, str) or len(output) < 32:
        return None
    # Compound/chained/piped/scripted commands must not have their combined stdout swallowed.
    if _RE_COMPOUND.search(command):
        return None
    head = _RE_LEADING_ENV.sub("", command.strip(), count=1)
    for pattern, fn in _FILTERS:
        try:
            if pattern.match(head):
                res = fn(output)
                if res is not None:
                    return res
        except Exception:
            continue
    return None
