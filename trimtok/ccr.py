"""trimtok CCR: Unified Two-Tier Content-Addressed Retrieval Store with Zstandard compression.

Tier 1 — in-memory LRU (OrderedDict, 512 entries).
Tier 2 — ONE SQLite WAL database in ~/.trimtok/, replacing thousands of per-slice files.
Refs are short Base62 aliases (#c1, #c2, ...); legacy 12-hex digests keep resolving.

Also supports directory-parameterized file-based operations (for testing/seaming).
Never raises: every entry point fails open gracefully.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

# Optional Zstandard compression
_ZSTD_AVAILABLE = False
_ZSTD_CCTX = None
_ZSTD_DCTX = None
try:
    import zstandard as zstd
    _ZSTD_CCTX = zstd.ZstdCompressor(level=3)
    _ZSTD_DCTX = zstd.ZstdDecompressor()
    _ZSTD_AVAILABLE = True
except Exception:
    pass

import zlib

_BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_MAX_MEM_ITEMS = 512
_HEX12_RE = re.compile(r"^[0-9a-f]{12,16}$")
_LOCK = threading.Lock()
_GLOBAL_STORE: FastCCRStore | None = None

_PUT_COUNT = 0
_FILE_MEM_LRU: OrderedDict[tuple[Path, str], tuple[str, float]] = OrderedDict()


def default_dir() -> Path:
    """Canonical store directory: TRIMTOK_DATA_DIR > ~/.trimtok.

    Auto-migrates legacy Hermes plugin data if ~/.trimtok is not yet present.
    """
    if os.environ.get("TRIMTOK_DATA_DIR"):
        return Path(os.environ["TRIMTOK_DATA_DIR"]).expanduser()

    home = Path(os.path.expanduser("~"))
    canonical_dir = home / ".trimtok"

    if not canonical_dir.exists():
        legacy_candidates = []
        if os.environ.get("LOCALAPPDATA"):
            legacy_candidates.append(Path(os.environ["LOCALAPPDATA"]) / "hermes" / "plugin-data" / "trimtok")
        legacy_candidates.append(home / ".hermes" / "plugin-data" / "trimtok")
        for legacy in legacy_candidates:
            if legacy.is_dir() and any(legacy.iterdir()):
                try:
                    import shutil
                    canonical_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(legacy, canonical_dir, dirs_exist_ok=True)
                    break
                except Exception:
                    pass

    return canonical_dir


def _to_base62(num: int) -> str:
    if num <= 0:
        return "0"
    out = []
    while num:
        num, rem = divmod(num, 62)
        out.append(_BASE62[rem])
    return "".join(reversed(out))


def _compress_payload(content_str: str) -> bytes | str:
    """Compress content with zstd or zlib if size > 1500 chars, else keep str."""
    raw = content_str.encode("utf-8")
    if len(raw) < 1500:
        return content_str
    if _ZSTD_AVAILABLE and _ZSTD_CCTX is not None:
        try:
            return b"zstd:" + _ZSTD_CCTX.compress(raw)
        except Exception:
            pass
    try:
        return b"zlib:" + zlib.compress(raw, level=6)
    except Exception:
        return content_str


def _decompress_payload(stored: Any) -> str | None:
    """Decompress zstd, zlib, or raw str content."""
    if stored is None:
        return None
    if isinstance(stored, str):
        return stored
    if isinstance(stored, (bytes, bytearray, memoryview)):
        b = bytes(stored)
        if b.startswith(b"zstd:") and _ZSTD_AVAILABLE and _ZSTD_DCTX is not None:
            try:
                return _ZSTD_DCTX.decompress(b[5:]).decode("utf-8", errors="replace")
            except Exception:
                pass
        if b.startswith(b"zlib:"):
            try:
                return zlib.decompress(b[5:]).decode("utf-8", errors="replace")
            except Exception:
                pass
        try:
            return b.decode("utf-8", errors="replace")
        except Exception:
            return None
    return str(stored)


class FastCCRStore:
    """Two-Tier content-addressed store: RAM LRU + SQLite WAL with Base62 aliases."""

    def __init__(self, db_dir: Path | None = None, max_mem_items: int = _MAX_MEM_ITEMS) -> None:
        self.base = Path(db_dir) if db_dir else default_dir()
        self.db_path = self.base / "trimtok_ccr_v2.sqlite3"
        self.max_mem_items = max_mem_items
        self._lock = threading.Lock()
        self._mem: OrderedDict[str, str] = OrderedDict()
        self._alias_mem: dict[str, str] = {}
        self._sha_alias: dict[str, str] = {}
        self._conn: sqlite3.Connection | None = None
        self._alias_seq = 0
        self._writable = False
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=5.0
            )
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA temp_store=MEMORY;")
            cur.execute("PRAGMA busy_timeout=3000;")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                " sha12 TEXT PRIMARY KEY, alias TEXT UNIQUE, content BLOB, created_at REAL)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alias ON chunks(alias);")
            cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
            row = cur.execute("SELECT value FROM meta WHERE key='alias_seq'").fetchone()
            self._alias_seq = int(row[0]) if row and str(row[0]).isdigit() else 0
            self._conn.commit()
            self._writable = True
        except Exception:
            self._conn = None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def put(self, content: str, *, ttl_hours: int = 24) -> str:
        """Store content, return Base62 alias (e.g. #c1). Never raises."""
        if not content:
            return ""
        sha12 = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        with self._lock:
            if sha12 in self._sha_alias:
                alias = self._sha_alias[sha12]
                self._mem.move_to_end(sha12)
                return alias

            if not self._writable or self._conn is None:
                self._alias_seq += 1
                alias = f"#c{_to_base62(self._alias_seq)}"
                self._set_mem(sha12, alias, content)
                return alias

            try:
                cur = self._conn.cursor()
                row = cur.execute("SELECT alias FROM chunks WHERE sha12=?", (sha12,)).fetchone()
                if row:
                    alias = row[0]
                    self._set_mem(sha12, alias, content)
                    return alias

                if self._conn.in_transaction:
                    self._conn.commit()
                cur.execute("BEGIN IMMEDIATE")
                # Re-check under lock in case another process inserted concurrently
                row = cur.execute("SELECT alias FROM chunks WHERE sha12=?", (sha12,)).fetchone()
                if row:
                    alias = row[0]
                    cur.execute("COMMIT")
                    self._set_mem(sha12, alias, content)
                    return alias

                cur.execute(
                    "INSERT INTO meta (key, value) VALUES ('alias_seq', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
                )
                seq_row = cur.execute("SELECT value FROM meta WHERE key='alias_seq'").fetchone()
                seq_num = int(seq_row[0]) if seq_row and str(seq_row[0]).isdigit() else 1
                self._alias_seq = max(self._alias_seq, seq_num)
                alias = f"#c{_to_base62(seq_num)}"

                now = time.time()
                stored_blob = _compress_payload(content)
                cur.execute(
                    "INSERT INTO chunks (sha12, alias, content, created_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(sha12) DO UPDATE SET content=excluded.content, created_at=excluded.created_at",
                    (sha12, alias, stored_blob, now),
                )
                # Opportunistic TTL sweep (at most once every 50 writes)
                if ttl_hours > 0 and (seq_num % 50 == 0):
                    expire_before = now - (ttl_hours * 3600.0)
                    cur.execute("DELETE FROM chunks WHERE created_at < ?", (expire_before,))

                cur.execute("COMMIT")
                self._set_mem(sha12, alias, content)
                return alias
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:
                    pass
                self._alias_seq += 1
                alias = f"#c{_to_base62(self._alias_seq)}"
                self._set_mem(sha12, alias, content)
                return alias

    def get(self, ref: str, *, ttl_hours: int = 24) -> str | None:
        """Retrieve original content by #ref (e.g. #c1) or sha12 hash."""
        if not ref:
            return None
        target = ref.strip()
        if re.match(r"^c[0-9A-Za-z]+$", target) and len(target) <= 8:
            target = f"#{target}"
        with self._lock:
            sha = self._alias_mem.get(target) if target.startswith("#") else target
            if sha and sha in self._mem and ttl_hours >= 24:
                self._mem.move_to_end(sha)
                return self._mem[sha]

            if not self._writable or self._conn is None:
                return self._mem.get(sha) if sha else None

            try:
                cur = self._conn.cursor()
                if target.startswith("#"):
                    row = cur.execute(
                        "SELECT sha12, alias, content, created_at FROM chunks WHERE alias=?",
                        (target,),
                    ).fetchone()
                else:
                    row = cur.execute(
                        "SELECT sha12, alias, content, created_at FROM chunks WHERE sha12=?",
                        (target,),
                    ).fetchone()

                if row:
                    sha12, row_alias, blob, created_at = row[0], row[1], row[2], row[3]
                    if (
                        ttl_hours > 0
                        and created_at is not None
                        and (time.time() - float(created_at)) > (ttl_hours * 3600.0)
                    ):
                        cur.execute("DELETE FROM chunks WHERE sha12=?", (sha12,))
                        self._conn.commit()
                        self._mem.pop(sha12, None)
                        self._sha_alias.pop(sha12, None)
                        if row_alias:
                            self._alias_mem.pop(row_alias, None)
                        return None
                    decomp = _decompress_payload(blob)
                    if decomp is not None:
                        alias = row_alias or (target if target.startswith("#") else f"#{sha12}")
                        self._set_mem(sha12, alias, decomp)
                        return decomp
            except Exception:
                return None
        return None

    def _set_mem(self, sha12: str, alias: str, content: str) -> None:
        self._mem[sha12] = content
        self._alias_mem[alias] = sha12
        self._sha_alias[sha12] = alias
        while len(self._mem) > self.max_mem_items:
            old_sha, _ = self._mem.popitem(last=False)
            old_alias = self._sha_alias.pop(old_sha, None)
            if old_alias:
                self._alias_mem.pop(old_alias, None)

    def stats(self) -> dict[str, int]:
        """Total entries and bytes cached."""
        count = 0
        total_bytes = 0
        with self._lock:
            if self._writable and self._conn is not None:
                try:
                    cur = self._conn.cursor()
                    row = cur.execute("SELECT COUNT(*), SUM(LENGTH(content)) FROM chunks").fetchone()
                    count = row[0] or 0
                    total_bytes = row[1] or 0
                except Exception:
                    pass
            if not count:
                count = len(self._mem)
                total_bytes = sum(len(v) for v in self._mem.values())
        return {"entries": count, "bytes": total_bytes}

    def purge(self, *, before_seconds: float = 0) -> int:
        """Purge stored chunks (all when before_seconds <= 0, or older than before_seconds)."""
        count = 0
        with self._lock:
            if before_seconds <= 0:
                self._mem.clear()
                self._alias_mem.clear()
                self._sha_alias.clear()
            if self._writable and self._conn is not None:
                try:
                    cur = self._conn.cursor()
                    if before_seconds > 0:
                        cutoff = time.time() - before_seconds
                        rows = cur.execute(
                            "SELECT sha12, alias FROM chunks WHERE created_at < ?",
                            (cutoff,),
                        ).fetchall()
                        count = len(rows)
                        for sha12, alias in rows:
                            self._mem.pop(sha12, None)
                            self._sha_alias.pop(sha12, None)
                            if alias:
                                self._alias_mem.pop(alias, None)
                        cur.execute("DELETE FROM chunks WHERE created_at < ?", (cutoff,))
                    else:
                        row = cur.execute("SELECT COUNT(*) FROM chunks").fetchone()
                        count = row[0] or 0
                        cur.execute("DELETE FROM chunks")
                    self._conn.commit()
                    self._conn.execute("VACUUM")
                except Exception:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
        return count


# ---------------------------------------------------------------------------
# File-Based CCR Operations (Legacy & Seaming support when dir is passed)
# ---------------------------------------------------------------------------
def _file_put(
    content: str,
    base: Path,
    max_entries: int = 1000,
    max_bytes: int = 50 * 1024 * 1024,
    ttl_hours: int = 24,
) -> str:
    global _PUT_COUNT
    temp_path: Path | None = None
    try:
        base.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8", "replace")
        digest = hashlib.sha256(data).hexdigest()[:12]
        path = base / (digest + ".txt")
        if not path.exists():
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=base, prefix=f".{digest}.", suffix=".tmp", delete=False
            ) as f:
                temp_path = Path(f.name)
                f.write(data)
                f.flush()
            os.replace(temp_path, path)
            temp_path = None
        with _LOCK:
            _PUT_COUNT += 1
            _FILE_MEM_LRU[(base, digest)] = (content, time.time())
            _FILE_MEM_LRU.move_to_end((base, digest))
            if len(_FILE_MEM_LRU) > _MAX_MEM_ITEMS:
                _FILE_MEM_LRU.popitem(last=False)
            if _PUT_COUNT % 16 == 0:
                _file_evict(base, max_entries, max_bytes, ttl_hours)
        return digest
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        return ""


def _file_get(digest: str, base: Path, ttl_hours: int = 24) -> str | None:
    try:
        clean = "".join(c for c in (digest or "") if c in "0123456789abcdef")[:16]
        if not clean:
            return None
        direct = base / (clean + ".txt")
        if direct.is_file():
            match = direct
        else:
            matches = sorted(base.glob(clean + "*.txt"))
            if len(matches) != 1:
                return None
            match = matches[0]
        cutoff = time.time() - max(0, ttl_hours) * 3600
        st = match.stat()
        if st.st_mtime < cutoff:
            match.unlink(missing_ok=True)
            with _LOCK:
                _FILE_MEM_LRU.pop((base, match.stem), None)
            return None
        key = (base, match.stem)
        with _LOCK:
            if key in _FILE_MEM_LRU and _FILE_MEM_LRU[key][1] >= st.st_mtime:
                _FILE_MEM_LRU.move_to_end(key)
                return _FILE_MEM_LRU[key][0]
        text = match.read_text(encoding="utf-8", errors="replace")
        with _LOCK:
            _FILE_MEM_LRU[key] = (text, st.st_mtime)
            _FILE_MEM_LRU.move_to_end(key)
            if len(_FILE_MEM_LRU) > _MAX_MEM_ITEMS:
                _FILE_MEM_LRU.popitem(last=False)
        return text
    except Exception:
        return None


def _file_evict(base: Path, max_entries: int, max_bytes: int, ttl_hours: int) -> None:
    try:
        cutoff = time.time() - (ttl_hours * 3600)
        files = []
        for p in base.glob("*.txt"):
            try:
                st = p.stat()
                if st.st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                else:
                    files.append((p, st))
            except Exception:
                pass
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


def _file_stats(base: Path, ttl_hours: int = 24) -> dict[str, int]:
    try:
        if not base.exists():
            return {"entries": 0, "bytes": 0}
        cutoff = time.time() - max(0, ttl_hours) * 3600
        files = []
        for p in base.glob("*.txt"):
            try:
                st = p.stat()
                if st.st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                else:
                    files.append((p, st))
            except Exception:
                pass
        return {"entries": len(files), "bytes": sum(st.st_size for _, st in files)}
    except Exception:
        return {"entries": 0, "bytes": 0}


def _file_purge(base: Path, before_seconds: float = 0) -> int:
    try:
        if not base.exists():
            return 0
        now = time.time()
        purged = 0
        for p in base.glob("*.txt"):
            if before_seconds <= 0 or (now - p.stat().st_mtime) > before_seconds:
                p.unlink(missing_ok=True)
                with _LOCK:
                    _FILE_MEM_LRU.pop((base, p.stem), None)
                purged += 1
        return purged
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Public Facade
# ---------------------------------------------------------------------------
def _get_store(base_dir: Path | None = None) -> FastCCRStore:
    target = Path(base_dir) if base_dir else default_dir()
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None or _GLOBAL_STORE.base != target:
        with _LOCK:
            if _GLOBAL_STORE is None or _GLOBAL_STORE.base != target:
                if _GLOBAL_STORE is not None:
                    _GLOBAL_STORE.close()
                _GLOBAL_STORE = FastCCRStore(target)
    return _GLOBAL_STORE


def close(dir: Path | None = None) -> None:
    global _GLOBAL_STORE
    with _LOCK:
        if _GLOBAL_STORE is not None:
            _GLOBAL_STORE.close()
            _GLOBAL_STORE = None


def put(content: str, *, dir: Path | None = None, ttl_hours: int = 24, **kwargs: Any) -> str:
    """Store content, return alias ref or digest. Never raises."""
    try:
        if dir is not None:
            return _file_put(content, Path(dir), ttl_hours=ttl_hours, **kwargs)
        return _get_store().put(content, ttl_hours=ttl_hours)
    except Exception:
        return ""


def get(ref: str, *, dir: Path | None = None, ttl_hours: int = 24, **kwargs: Any) -> str | None:
    """Retrieve content by #c... alias or 12-char hex digest."""
    try:
        base = Path(dir) if dir else default_dir()
        if dir is not None or base.exists():
            file_res = _file_get(ref, base, ttl_hours=ttl_hours)
            if file_res is not None:
                return file_res
        return _get_store(base).get(ref, ttl_hours=ttl_hours)
    except Exception:
        return None


def stats(*, dir: Path | None = None, ttl_hours: int = 24, **kwargs: Any) -> dict[str, int]:
    try:
        if dir is not None:
            return _file_stats(Path(dir), ttl_hours=ttl_hours)
        return _get_store().stats()
    except Exception:
        return {"entries": 0, "bytes": 0}


def get_stats(*, dir: Path | None = None, **kwargs: Any) -> dict[str, int]:
    return stats(dir=dir, **kwargs)


def purge(*, dir: Path | None = None, before_seconds: float = 0, **kwargs: Any) -> int:
    try:
        if dir is not None:
            return _file_purge(Path(dir), before_seconds=before_seconds)
        return _get_store().purge(before_seconds=before_seconds)
    except Exception:
        return 0


def resolve_hash(ref: str) -> str | None:
    """Resolve an alias #c... or hex digest to the canonical 12-hex hash."""
    if not ref:
        return None
    target = ref.strip()
    if re.match(r"^c[0-9A-Za-z]+$", target) and len(target) <= 8:
        target = f"#{target}"
    store = _get_store()
    sha = store._alias_mem.get(target)
    if sha:
        return sha
    if target.startswith("#") and store._writable and store._conn is not None:
        with store._lock:
            try:
                row = store._conn.execute(
                    "SELECT sha12 FROM chunks WHERE alias=?", (target,)
                ).fetchone()
                if row and row[0]:
                    store._alias_mem[target] = row[0]
                    store._sha_alias[row[0]] = target
                    return row[0]
            except Exception:
                pass
    return target if _HEX12_RE.match(target) else None
