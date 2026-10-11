"""trimtok CCR: content-addressed retrieval cache. One file per sha256 slice.

Concurrency-safe: key = content hash, writes are exclusive-create.
Eviction: LRU by mtime when over entry/byte caps. Never raises.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

_PUT_COUNT = 0


def default_dir() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]) / "plugin-data" / "trimtok"
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return get_hermes_home() / "plugin-data" / "trimtok"
    except Exception:
        return Path(os.path.expanduser("~")) / ".hermes" / "plugin-data" / "trimtok"


def put(
    content: str,
    *,
    dir: Path | None = None,
    max_entries: int = 1000,
    max_bytes: int = 50 * 1024 * 1024,
    ttl_hours: int = 24,
) -> str:
    """Store original, return 12-char digest. Never raises ('' on failure)."""
    global _PUT_COUNT
    try:
        base = Path(dir) if dir else default_dir()
        base.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8", "replace")
        digest = hashlib.sha256(data).hexdigest()[:12]
        path = base / (digest + ".txt")
        if not path.exists():
            # exclusive create: concurrent same-content writers, one wins
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
        _PUT_COUNT += 1
        if _PUT_COUNT % 16 == 0:
            _evict(base, max_entries, max_bytes, ttl_hours)
        return digest
    except Exception:
        return ""


def get(digest: str, *, dir: Path | None = None) -> str | None:
    """Fetch original by digest prefix (unique prefix ok). None if missing/expired."""
    try:
        base = Path(dir) if dir else default_dir()
        clean = "".join(c for c in (digest or "") if c in "0123456789abcdef")[:16]
        if not clean:
            return None
        # Fast path: exact digest file exists directly (bypasses glob)
        direct = base / (clean + ".txt")
        if direct.is_file():
            return direct.read_text(encoding="utf-8", errors="replace")
        # Prefix lookup fallback
        matches = sorted(base.glob(clean + "*.txt"))
        if len(matches) != 1:
            return None
        return matches[0].read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def purge(*, dir: Path | None = None, before_seconds: float = 0) -> int:
    """Manually purge CCR store files. Returns count of purged files."""
    try:
        base = Path(dir) if dir else default_dir()
        if not base.exists():
            return 0
        now = time.time()
        purged = 0
        for p in base.glob("*.txt"):
            if before_seconds <= 0 or (now - p.stat().st_mtime) > before_seconds:
                p.unlink(missing_ok=True)
                purged += 1
        return purged
    except Exception:
        return 0


def stats(*, dir: Path | None = None) -> dict:
    """Return count and total bytes of cached originals."""
    try:
        base = Path(dir) if dir else default_dir()
        if not base.exists():
            return {"entries": 0, "bytes": 0}
        files = list(base.glob("*.txt"))
        total = sum(p.stat().st_size for p in files)
        return {"entries": len(files), "bytes": total}
    except Exception:
        return {"entries": 0, "bytes": 0}


def _evict(base: Path, max_entries: int, max_bytes: int, ttl_hours: int) -> None:
    try:
        now = time.time()
        cutoff = now - (ttl_hours * 3600)
        files: list[tuple[Path, os.stat_result]] = []
        for p in base.glob("*.txt"):
            try:
                st = p.stat()
                if st.st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                else:
                    files.append((p, st))
            except Exception:
                pass
        # oldest first
        files.sort(key=lambda t: t[1].st_mtime)
        while len(files) > max_entries:
            p, _ = files.pop(0)
            p.unlink(missing_ok=True)
        total = sum(st.st_size for _, st in files)
        while files and total > max_bytes:
            p, st = files.pop(0)
            p.unlink(missing_ok=True)
            total -= st.st_size
    except Exception:
        pass
