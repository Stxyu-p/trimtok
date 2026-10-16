"""trimtok CCR: content-addressed retrieval cache. One file per sha256 slice.

Concurrency-safe: key = content hash, writes publish atomically.
Eviction: LRU by mtime when over entry/byte caps. Never raises.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

_PUT_COUNT = 0
_MEM_LRU: OrderedDict[tuple[Path, str], tuple[str, float]] = OrderedDict()
_MAX_MEM_LRU = 512
_LOCK = threading.Lock()


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
    temp_path: Path | None = None
    try:
        base = Path(dir) if dir else default_dir()
        base.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8", "replace")
        digest = hashlib.sha256(data).hexdigest()[:12]
        path = base / (digest + ".txt")
        if not path.exists():
            # Publish only after the complete payload has reached a temp file.
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=base, prefix=f".{digest}.", suffix=".tmp", delete=False
            ) as f:
                temp_path = Path(f.name)
                f.write(data)
                f.flush()
            # Concurrent writers for the same digest contain identical bytes;
            # atomic replacement prevents readers from observing a partial file.
            os.replace(temp_path, path)
            temp_path = None
        should_evict = False
        with _LOCK:
            _PUT_COUNT += 1
            _MEM_LRU[(base, digest)] = (content, time.time())
            _MEM_LRU.move_to_end((base, digest))
            if len(_MEM_LRU) > _MAX_MEM_LRU:
                _MEM_LRU.popitem(last=False)
            should_evict = (_PUT_COUNT % 16 == 0)
        if should_evict:
            _evict(base, max_entries, max_bytes, ttl_hours)
        return digest
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        return ""


def get(
    digest: str,
    *,
    dir: Path | None = None,
    ttl_hours: int = 24,
) -> str | None:
    """Fetch original by digest prefix (unique prefix ok). None if missing/expired."""
    try:
        base = Path(dir) if dir else default_dir()
        clean = "".join(c for c in (digest or "") if c in "0123456789abcdef")[:16]
        if not clean:
            return None
        # Fast path: exact digest file exists directly (bypasses glob)
        direct = base / (clean + ".txt")
        if direct.is_file():
            match = direct
        else:
            # Prefix lookup fallback
            matches = sorted(base.glob(clean + "*.txt"))
            if len(matches) != 1:
                return None
            match = matches[0]
        cutoff = time.time() - max(0, ttl_hours) * 3600
        st = match.stat()
        if _is_expired(st, cutoff):
            match.unlink(missing_ok=True)
            with _LOCK:
                _MEM_LRU.pop((base, match.stem), None)
            return None
        key = (base, match.stem)
        with _LOCK:
            if key in _MEM_LRU and _MEM_LRU[key][1] >= st.st_mtime:
                _MEM_LRU.move_to_end(key)
                return _MEM_LRU[key][0]
        text = match.read_text(encoding="utf-8", errors="replace")
        with _LOCK:
            _MEM_LRU[key] = (text, st.st_mtime)
            _MEM_LRU.move_to_end(key)
            if len(_MEM_LRU) > _MAX_MEM_LRU:
                _MEM_LRU.popitem(last=False)
        return text
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
                with _LOCK:
                    _MEM_LRU.pop((base, p.stem), None)
                purged += 1
        return purged
    except Exception:
        return 0


def stats(*, dir: Path | None = None, ttl_hours: int = 24) -> dict:
    """Return count and total bytes of cached originals."""
    try:
        base = Path(dir) if dir else default_dir()
        if not base.exists():
            return {"entries": 0, "bytes": 0}
        cutoff = time.time() - max(0, ttl_hours) * 3600
        files = _live_files(base, cutoff)
        total = sum(stat.st_size for _, stat in files)
        return {"entries": len(files), "bytes": total}
    except Exception:
        return {"entries": 0, "bytes": 0}


def _evict(base: Path, max_entries: int, max_bytes: int, ttl_hours: int) -> None:
    try:
        now = time.time()
        cutoff = now - (ttl_hours * 3600)
        files = _live_files(base, cutoff)
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


def _is_expired(stat: os.stat_result, cutoff: float) -> bool:
    return stat.st_mtime < cutoff


def _live_files(base: Path, cutoff: float) -> list[tuple[Path, os.stat_result]]:
    """Return non-expired cache entries while deleting expired ones."""
    files: list[tuple[Path, os.stat_result]] = []
    for path in base.glob("*.txt"):
        try:
            stat = path.stat()
            if _is_expired(stat, cutoff):
                path.unlink(missing_ok=True)
            else:
                files.append((path, stat))
        except Exception:
            pass
    return files
