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

import math
import os
import shutil
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
MAX_TIMEOUT = 60.0


def run(
    ref: str,
    code: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_out: int = MAX_OUT,
    ttl_hours: int = 24,
) -> dict[str, Any]:
    """Run Python `code` with the payload behind `ref` in $TRIMTOK_PAYLOAD."""
    t0 = time.perf_counter()
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "output": "code is empty", "exit_code": 2, "ms": 0.0}

    try:
        eff_timeout = float(timeout)
        if not math.isfinite(eff_timeout) or eff_timeout <= 0.0:
            eff_timeout = DEFAULT_TIMEOUT
    except Exception:
        eff_timeout = DEFAULT_TIMEOUT
    eff_timeout = max(0.1, min(MAX_TIMEOUT, eff_timeout))

    try:
        eff_max_out = max(1, int(max_out))
    except Exception:
        eff_max_out = MAX_OUT

    payload = ccr.get(ref, ttl_hours=ttl_hours)
    if payload is None:
        return {"ok": False, "output": f"ref not found or expired: {ref}", "exit_code": 1, "ms": 0.0}

    raw_bytes = payload.encode("utf-8", "replace")
    payload_bytes = len(raw_bytes)
    tmpdir = tempfile.mkdtemp(prefix="trimtok-exec-")
    path = os.path.join(tmpdir, "payload.txt")
    try:
        # Write exact UTF-8 bytes without CRLF translation so byte offsets match TRIMTOK_PAYLOAD_BYTES
        with open(path, "wb") as fh:
            fh.write(raw_bytes)

        env = dict(os.environ)
        env["TRIMTOK_PAYLOAD"] = path
        env["TRIMTOK_PAYLOAD_BYTES"] = str(payload_bytes)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8:replace"
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=tmpdir,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=eff_timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "output": f"timeout after {eff_timeout}s",
                "exit_code": 124,
                "payload_bytes": payload_bytes,
                "truncated": False,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        body = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if err:
            body = f"{body}\n[stderr]\n{err}" if body else err
        cut = len(body) > eff_max_out
        if cut:
            body = body[:eff_max_out] + f"\n[trimtok] output truncated at {eff_max_out} chars"
        return {
            "ok": proc.returncode == 0,
            "output": body,
            "exit_code": proc.returncode,
            "payload_bytes": payload_bytes,
            "truncated": cut,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 - a tool must answer, not raise
        return {"ok": False, "output": f"{type(exc).__name__}: {exc}", "exit_code": 1, "ms": 0.0}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
