"""Pre-execution command rewriting for noisy developer CLI tools."""

from __future__ import annotations

import os
import re
import shlex

_PYTEST_VERBOSITY_RE = re.compile(r"^-[vq]+$")


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
        # Never alter explicit verbose, quiet, custom traceback, or `--` pass-through args
        if "--" in args:
            return argv
        if any(
            _PYTEST_VERBOSITY_RE.match(a)
            or a in ("--verbose", "--quiet", "--tb")
            or a.startswith("--tb=")
            for a in args
        ):
            return argv
        return [*argv, "-q", "--tb=short"]

    if head == "git" and args[:1] == ["log"]:
        # Insert formatting flags BEFORE non-option path/ref arguments (git rejects trailing flags)
        if not any(a.startswith("-") for a in args[1:]):
            return ["git", "log", "--oneline", "-n", "25", *args[1:]]

    if head == "cargo" and args[:1] == ["test"]:
        if "--" not in args:
            return [*argv, "--", "--quiet"]

    return argv


def rewrite_command(cmd: str) -> str:
    raw = cmd.strip()
    if not raw:
        return cmd
    # On Windows, POSIX shlex.split strips backslashes in paths (`C:\foo` -> `C:foo`)
    if os.name == "nt" and "\\" in raw:
        return cmd
    try:
        parts = shlex.split(raw)
    except Exception:
        return cmd
    if not parts:
        return cmd
    out = rewrite_argv(parts)
    return cmd if out == parts else shlex.join(out)
