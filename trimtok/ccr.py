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
_HEX12_RE = re.compile(r"^[0-9a-f]{12,64}$")
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
            with _LOCK:
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
                with _LOCK:
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


def _is_hex_digest(s: str) -> bool:
    """True if s looks like a hex content digest (>=8 hex chars)."""
    return len(s) >= 8 and all(c in "0123456789abcdef" for c in s.lower())


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize user search query for safe SQLite FTS5 MATCH execution."""
    if not query:
        return ""
    tokens = re.findall(r'[a-zA-Z0-9_\u0E00-\u0E7F]+', query)
    if not tokens:
        return ""
    return " ".join(f'"{tok}"' for tok in tokens)


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
                str(self.db_path), check_same_thread=False, timeout=10.0, isolation_level=None
            )
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA temp_store=MEMORY;")
            cur.execute("PRAGMA busy_timeout=10000;")
            cur.execute("PRAGMA journal_size_limit=4194304;")
            cur.execute("PRAGMA wal_autocheckpoint=100;")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                " sha TEXT PRIMARY KEY, alias TEXT UNIQUE, content BLOB, created_at REAL, pinned INTEGER DEFAULT 0)"
            )
            try:
                cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(alias, content);")
            except Exception:
                pass
            # Migrate legacy schema (sha12 column) to new (sha) column and ensure pinned exists.
            try:
                cols = {r[1] for r in cur.execute("PRAGMA table_info(chunks)").fetchall()}
                if "sha" not in cols and "sha12" in cols:
                    cur.execute("ALTER TABLE chunks RENAME COLUMN sha12 TO sha;")
                if "pinned" not in cols:
                    cur.execute("ALTER TABLE chunks ADD COLUMN pinned INTEGER DEFAULT 0;")
            except Exception:
                pass
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alias ON chunks(alias);")
            cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
            row = cur.execute("SELECT value FROM meta WHERE key='alias_seq'").fetchone()
            self._alias_seq = int(row[0]) if row and str(row[0]).isdigit() else 0
            self._writable = True
        except Exception:
            self._conn = None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                except Exception:
                    pass
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def put(self, content: str, *, ttl_hours: int = 24) -> str:
        """Store content, return Base62 alias (e.g. #c1). Never raises."""
        if not content:
            return ""
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if sha in self._sha_alias:
                alias = self._sha_alias[sha]
                self._mem.move_to_end(sha)
                return alias

            if not self._writable or self._conn is None:
                # Fail open: never emit a retrieval marker that cannot be
                # resolved after a process restart (in-memory-only alias).
                return ""

            try:
                cur = self._conn.cursor()
                cur.execute("BEGIN IMMEDIATE")
                row = cur.execute("SELECT alias FROM chunks WHERE sha=?", (sha,)).fetchone()
                if row:
                    alias = row[0]
                    cur.execute("COMMIT")
                    self._set_mem(sha, alias, content)
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
                    "INSERT INTO chunks (sha, alias, content, created_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(sha) DO UPDATE SET content=excluded.content, created_at=excluded.created_at",
                    (sha, alias, stored_blob, now),
                )
                try:
                    cur.execute("DELETE FROM chunks_fts WHERE alias = ?", (alias,))
                    cur.execute("INSERT INTO chunks_fts (alias, content) VALUES (?, ?)", (alias, content))
                except Exception:
                    pass
                # Opportunistic TTL sweep (at most once every 50 writes)
                if ttl_hours > 0 and (seq_num % 50 == 0):
                    expire_before = now - (ttl_hours * 3600.0)
                    try:
                        for row in cur.execute(
                            "SELECT alias FROM chunks WHERE created_at < ? AND (pinned IS NULL OR pinned = 0)",
                            (expire_before,),
                        ).fetchall():
                            if row and row[0]:
                                cur.execute("DELETE FROM chunks_fts WHERE alias = ?", (row[0],))
                    except Exception:
                        pass
                    cur.execute("DELETE FROM chunks WHERE created_at < ? AND (pinned IS NULL OR pinned = 0)", (expire_before,))

                cur.execute("COMMIT")
                self._set_mem(sha, alias, content)
                return alias
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:
                    pass
                # Strict fail-open: never emit an unresolvable in-memory alias!
                return ""

    def get(self, ref: str, *, ttl_hours: int = 24) -> str | None:
        """Retrieve original content by #ref (e.g. #c1) or sha hash."""
        if not ref:
            return None
        target = ref.strip()
        if re.match(r"^c[0-9A-Za-z]+$", target) and len(target) <= 8:
            target = f"#{target}"
        with self._lock:
            # A "#<hex>" ref may be a digest (content hash) rather than a CCR
            # alias; resolve by sha prefix in that case.
            is_alias = target.startswith("#") and not _is_hex_digest(target.lstrip("#"))
            if is_alias:
                sha = self._alias_mem.get(target)
            else:
                sha = target.lstrip("#")
            if sha and sha in self._mem and ttl_hours >= 24:
                self._mem.move_to_end(sha)
                return self._mem[sha]

            if not self._writable or self._conn is None:
                return self._mem.get(sha) if sha else None

            try:
                cur = self._conn.cursor()
                if is_alias:
                    row = cur.execute(
                        "SELECT sha, alias, content, created_at, pinned FROM chunks WHERE alias=?",
                        (target,),
                    ).fetchone()
                elif len(sha) < 64:
                    row = cur.execute(
                        "SELECT sha, alias, content, created_at, pinned FROM chunks WHERE substr(sha,1,?)=?",
                        (len(sha), sha),
                    ).fetchone()
                else:
                    row = cur.execute(
                        "SELECT sha, alias, content, created_at, pinned FROM chunks WHERE sha=?",
                        (sha,),
                    ).fetchone()

                if row:
                    sha, row_alias, blob, created_at = row[0], row[1], row[2], row[3]
                    is_pinned = bool(row[4]) if len(row) > 4 else False
                    if (
                        ttl_hours > 0
                        and not is_pinned
                        and created_at is not None
                        and (time.time() - float(created_at)) > (ttl_hours * 3600.0)
                    ):
                        cur.execute("DELETE FROM chunks WHERE sha=?", (sha,))
                        if row_alias:
                            try:
                                cur.execute("DELETE FROM chunks_fts WHERE alias=?", (row_alias,))
                            except Exception:
                                pass
                        self._conn.commit()
                        self._mem.pop(sha, None)
                        self._sha_alias.pop(sha, None)
                        if row_alias:
                            self._alias_mem.pop(row_alias, None)
                        return None
                    decomp = _decompress_payload(blob)
                    if decomp is not None:
                        alias = row_alias or (target if target.startswith("#") else f"#{sha}")
                        self._set_mem(sha, alias, decomp)
                        return decomp
            except Exception:
                return None
        return None

    def _set_mem(self, sha: str, alias: str, content: str) -> None:
        self._mem[sha] = content
        self._alias_mem[alias] = sha
        self._sha_alias[sha] = alias
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

    def evict_to_size(self, max_bytes: int) -> int:
        """Delete the oldest chunks until stored bytes are under `max_bytes`."""
        if max_bytes <= 0:
            return 0
        evicted = 0
        with self._lock:
            if not (self._writable and self._conn is not None):
                return 0
            try:
                cur = self._conn.cursor()
                row = cur.execute("SELECT COALESCE(SUM(LENGTH(content)), 0) FROM chunks").fetchone()
                total = int(row[0] or 0)
                if total <= max_bytes:
                    return 0
                for sha, alias, size in cur.execute(
                    "SELECT sha, alias, LENGTH(content) FROM chunks ORDER BY created_at ASC"
                ).fetchall():
                    if total <= max_bytes:
                        break
                    self._mem.pop(sha, None)
                    self._sha_alias.pop(sha, None)
                    if alias:
                        self._alias_mem.pop(alias, None)
                        try:
                            cur.execute("DELETE FROM chunks_fts WHERE alias = ?", (alias,))
                        except Exception:
                            pass
                    cur.execute("DELETE FROM chunks WHERE sha = ?", (sha,))
                    total -= int(size or 0)
                    evicted += 1
                self._conn.commit()
                if evicted:
                    self._conn.execute("VACUUM")
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
        return evicted

    def set_ttl_hours(self, hours: int) -> None:
        """Persist the retention window in the store's meta table."""
        if hours <= 0:
            return
        with self._lock:
            if not (self._writable and self._conn is not None):
                return
            try:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES('ttl_hours', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(int(hours)),),
                )
                self._conn.commit()
            except Exception:
                pass

    def ttl_hours(self, default: int = 24) -> int:
        with self._lock:
            if self._writable and self._conn is not None:
                try:
                    row = self._conn.execute(
                        "SELECT value FROM meta WHERE key='ttl_hours'"
                    ).fetchone()
                    if row and str(row[0]).isdigit():
                        return int(row[0])
                except Exception:
                    pass
        return default

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
                            "SELECT sha, alias FROM chunks WHERE created_at < ?",
                            (cutoff,),
                        ).fetchall()
                        count = len(rows)
                        for sha, alias in rows:
                            self._mem.pop(sha, None)
                            self._sha_alias.pop(sha, None)
                            if alias:
                                self._alias_mem.pop(alias, None)
                                try:
                                    cur.execute("DELETE FROM chunks_fts WHERE alias = ?", (alias,))
                                except Exception:
                                    pass
                        cur.execute("DELETE FROM chunks WHERE created_at < ?", (cutoff,))
                    else:
                        row = cur.execute("SELECT COUNT(*) FROM chunks").fetchone()
                        count = row[0] or 0
                        cur.execute("DELETE FROM chunks")
                        try:
                            cur.execute("DELETE FROM chunks_fts")
                        except Exception:
                            pass
                    self._conn.commit()
                    self._conn.execute("VACUUM")
                except Exception:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
        return count

    def pin(self, ref: str) -> bool:
        """Mark a chunk as pinned so it will not be deleted by TTL sweep."""
        if not ref:
            return False
        target = ref.strip()
        if re.match(r"^c[0-9A-Za-z]+$", target) and len(target) <= 8:
            target = f"#{target}"
        with self._lock:
            if not self._writable or self._conn is None:
                return False
            try:
                cur = self._conn.cursor()
                if target.startswith("#c") or (target.startswith("#") and not _is_hex_digest(target.lstrip("#"))):
                    cur.execute("UPDATE chunks SET pinned = 1 WHERE alias = ?", (target,))
                else:
                    sha = target.lstrip("#")
                    cur.execute("UPDATE chunks SET pinned = 1 WHERE sha = ? OR substr(sha,1,?) = ?", (sha, len(sha), sha))
                self._conn.commit()
                return cur.rowcount > 0
            except Exception:
                return False

    def unpin(self, ref: str) -> bool:
        """Unpin a chunk, allowing it to expire according to TTL."""
        if not ref:
            return False
        target = ref.strip()
        if re.match(r"^c[0-9A-Za-z]+$", target) and len(target) <= 8:
            target = f"#{target}"
        with self._lock:
            if not self._writable or self._conn is None:
                return False
            try:
                cur = self._conn.cursor()
                if target.startswith("#c") or (target.startswith("#") and not _is_hex_digest(target.lstrip("#"))):
                    row = cur.execute("SELECT sha FROM chunks WHERE alias = ?", (target,)).fetchone()
                    sha = row[0] if row else ""
                    cur.execute("UPDATE chunks SET pinned = 0 WHERE alias = ?", (target,))
                else:
                    sha = target.lstrip("#")
                    cur.execute("UPDATE chunks SET pinned = 0 WHERE sha = ? OR substr(sha,1,?) = ?", (sha, len(sha), sha))
                if sha:
                    self._mem.pop(sha, None)
                self._conn.commit()
                return cur.rowcount > 0
            except Exception:
                return False

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search stored CCR payloads via SQLite FTS5 BM25. Never raises."""
        fts_query = _sanitize_fts5_query(query)
        if not fts_query:
            return []
        with self._lock:
            if not (self._writable and self._conn is not None):
                return []
            try:
                cur = self._conn.cursor()
                sql = (
                    "SELECT alias, snippet(chunks_fts, 1, '<b>', '</b>', '...', 15), rank "
                    "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?"
                )
                hits: list[dict[str, Any]] = []
                for row in cur.execute(sql, (fts_query, max(1, limit))).fetchall():
                    hits.append({
                        "alias": row[0] or "",
                        "snippet": row[1] if row[1] else "",
                        "rank": float(row[2]) if row[2] is not None else 0.0,
                    })
                return hits
            except Exception:
                return []


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
            if dir is None or _GLOBAL_STORE.base == Path(dir):
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
        if not ref:
            return None
        target = ref.strip()
        base = Path(dir) if dir else default_dir()
        is_alias = target.startswith("#c") or bool(re.match(r"^c[0-9A-Za-z]+$", target) and len(target) <= 8)
        if is_alias:
            return _get_store(base).get(target, ttl_hours=ttl_hours)

        # For hex digests: check SQLite store first, only fall back to _file_get if not in SQLite
        store_res = _get_store(base).get(target, ttl_hours=ttl_hours)
        if store_res is not None:
            return store_res
        return _file_get(target, base, ttl_hours=ttl_hours)
    except Exception:
        return None


def evict_to_size(max_bytes: int, *, dir: Path | None = None) -> int:
    """Drop the oldest cached chunks until the store fits under `max_bytes`."""
    try:
        if dir is not None:
            base = Path(dir)
            files = sorted(
                (p for p in base.glob("*.txt") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
            total = sum(p.stat().st_size for p in files)
            evicted = 0
            for p in files:
                if total <= max_bytes:
                    break
                try:
                    total -= p.stat().st_size
                    p.unlink()
                    evicted += 1
                except Exception:
                    pass
            return evicted
        return _get_store().evict_to_size(max_bytes)
    except Exception:
        return 0


def set_ttl_hours(hours: int, *, dir: Path | None = None) -> None:
    """Persist the CCR retention window (hours)."""
    try:
        if dir is not None:
            meta = Path(dir) / "ttl_hours.txt"
            meta.write_text(str(int(hours)), encoding="utf-8")
            return
        _get_store().set_ttl_hours(int(hours))
    except Exception:
        pass


def stats(*, dir: Path | None = None, ttl_hours: int = 24, **kwargs: Any) -> dict[str, int]:
    try:
        if dir is not None:
            return _file_stats(Path(dir), ttl_hours=ttl_hours)
        return _get_store().stats()
    except Exception:
        return {"entries": 0, "bytes": 0}



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
                    "SELECT sha FROM chunks WHERE alias=?", (target,)
                ).fetchone()
                if row and row[0]:
                    store._alias_mem[target] = row[0]
                    store._sha_alias[row[0]] = target
                    return row[0]
            except Exception:
                pass
    return target if _HEX12_RE.match(target) else None


def pin(ref: str, *, dir: Path | None = None) -> bool:
    """Pin a CCR chunk by ref or hash to prevent TTL eviction."""
    try:
        base = Path(dir) if dir else default_dir()
        return _get_store(base).pin(ref)
    except Exception:
        return False


def unpin(ref: str, *, dir: Path | None = None) -> bool:
    """Unpin a CCR chunk by ref or hash."""
    try:
        base = Path(dir) if dir else default_dir()
        return _get_store(base).unpin(ref)
    except Exception:
        return False


def search(query: str, limit: int = 5, *, dir: Path | None = None) -> list[dict[str, Any]]:
    """Search stored CCR payloads via SQLite FTS5 BM25."""
    try:
        base = Path(dir) if dir else default_dir()
        return _get_store(base).search(query, limit=limit)
    except Exception:
        return []
