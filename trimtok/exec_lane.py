"""Compute over a stored original without materialising it in the context window.

A `#ref` today only supports retrieve(): the whole text goes back into the
prompt. For an aggregate question ("how many rows", "which shape dominates")
that is the wrong shape — measured in the competitor lab, the same 627 KB JSON
question cost 224,523 bytes as a compressed table and 155 bytes as a script
result. This is that lane, over CCR refs.

The subprocess is a *context* boundary, not a security boundary: the caller is
an agent that already has a terminal. It exists so the payload never has to be
read back into the prompt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Any

try:
    from . import ccr
except ImportError:  # pragma: no cover - flat import for scripts
    import ccr  # type: ignore

MAX_OUT = 4000
DEFAULT_TIMEOUT = 10.0


def run(
    ref: str,
    code: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_out: int = MAX_OUT,
) -> dict[str, Any]:
    """Run Python `code` with the payload behind `ref` in $TRIMTOK_PAYLOAD.

    Python only: trimtok is a Python package, so the interpreter is guaranteed
    present, and a shell lane would need two different env-var syntaxes
    ($VAR on POSIX, %VAR% on Windows) for no capability Python lacks.
    """
    t0 = time.perf_counter()
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "output": "code is empty", "exit_code": 2, "ms": 0.0}

    payload = ccr.get(ref)
    if payload is None:
        return {"ok": False, "output": f"ref not found or expired: {ref}", "exit_code": 1, "ms": 0.0}

    tmpdir = tempfile.mkdtemp(prefix="trimtok-exec-")
    path = os.path.join(tmpdir, "payload.txt")
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(payload)

        env = dict(os.environ)
        env["TRIMTOK_PAYLOAD"] = path
        env["TRIMTOK_PAYLOAD_BYTES"] = str(len(payload.encode("utf-8", "replace")))
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(0.1, float(timeout)),
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "output": f"timeout after {timeout}s",
                "exit_code": 124,
                "payload_bytes": len(payload),
                "ms": (time.perf_counter() - t0) * 1000,
            }

        body = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if err:
            body = f"{body}\n[stderr]\n{err}" if body else err
        cut = len(body) > max_out
        if cut:
            body = body[:max_out] + f"\n[trimtok] output truncated at {max_out} chars"
        return {
            "ok": proc.returncode == 0,
            "output": body,
            "exit_code": proc.returncode,
            "payload_bytes": len(payload),
            "truncated": cut,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 - a tool must answer, not raise
        return {"ok": False, "output": f"{type(exc).__name__}: {exc}", "exit_code": 1, "ms": 0.0}
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass
