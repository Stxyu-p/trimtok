"""Pre-execution command rewriting for noisy developer CLI tools."""

from __future__ import annotations

import shlex

def rewrite_argv(argv: list[str]) -> list[str]:
    """Append noise-reducing flags to an argv list without re-parsing a string.

    argv must stay a list: joining and re-splitting loses the caller's quoting
    (`pytest -k "not slow"` becomes four arguments and silently changes which
    tests run).
    """
    if not argv:
        return argv
    if any(p in a for a in argv for p in ("|", ";", "&&", "||", ">", "<")):
        # Pipelines and redirects belong to the caller's shell.
        return argv
    head, args = argv[0], argv[1:]

    if head == "pytest":
        # Never alter explicit verbose or custom traceback settings
        if any(a in ("-v", "--verbose") for a in args) or any(a.startswith("--tb=") for a in args):
            return argv
        if any(a in ("-q", "--quiet") for a in args):
            return argv
        return [*argv, "-q", "--tb=short"]

    if head == "git" and args[:1] == ["log"]:
        # Only inject on bare git log without user-specified formatting or limit
        if not any(a.startswith("-") for a in args[1:]):
            return [*argv, "--oneline", "-n", "25"]

    if head == "cargo" and args[:1] == ["test"]:
        if "--" not in args:
            return [*argv, "--", "--quiet"]

    return argv


def rewrite_command(cmd: str) -> str:
    raw = cmd.strip()
    if not raw:
        return cmd
    try:
        parts = shlex.split(raw)
    except Exception:
        return cmd
    if not parts:
        return cmd
    out = rewrite_argv(parts)
    return cmd if out == parts else shlex.join(out)
