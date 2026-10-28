"""Pre-execution command rewriting for noisy developer CLI tools."""

from __future__ import annotations

import shlex

def rewrite_command(cmd: str) -> str:
    raw = cmd.strip()
    if not raw or any(p in raw for p in ("|", ";", "&&", "||", ">", "<")):
        # Avoid breaking pipelines where downstream tools expect exact formatting
        return cmd

    try:
        parts = shlex.split(raw)
    except Exception:
        return cmd

    if not parts:
        return cmd

    head = parts[0]
    args = parts[1:]

    if head == "pytest":
        # Never alter explicit verbose or custom traceback settings
        if any(a in ("-v", "--verbose") for a in args) or any(a.startswith("--tb=") for a in args):
            return cmd
        has_quiet = any(a in ("-q", "--quiet") for a in args)
        if has_quiet:
            return cmd
        return f"{raw} -q --tb=short"

    elif head == "git" and len(args) >= 1 and args[0] == "log":
        # Only inject on bare git log without user-specified formatting or limit
        has_flags = any(a.startswith("-") for a in args[1:])
        if not has_flags:
            return f"{raw} --oneline -n 25"

    elif head == "cargo" and len(args) >= 1 and args[0] == "test":
        if "--" not in args:
            return f"{raw} -- --quiet"

    return cmd
