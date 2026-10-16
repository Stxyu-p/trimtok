"""trimtok CCR v2: Two-Tier content-addressed store (Trimtok 2.0 Phase 1).

Tier 1 — in-memory LRU (OrderedDict, 512 entries).
Tier 2 — ONE SQLite WAL database per plugin-data dir, replacing thousands of
per-slice NTFS files. Refs are short Base62 aliases (#c1, #c2, ...); legacy
12-hex digests keep resolving, and pre-v2 file-store entries still read
through the fallback, so old markers never break.

Never raises: every entry point falls open. On SQLite failure a call falls
back to the legacy file store (ccr.py); repeated lock errors flip a sticky
flag that pins the fallback for the process.

ponytail: aliases are store-global, not session-scoped as the plan sketch
says — gateway and CLI processes share this directory, and a per-session
counter would mint duplicate #c1 refs that resolve to the wrong payload.
Cost: ids grow with total stored blocks. Kill-criteria: if SQLite locking
trips the fallback more than once, the plan wants an append-only binary log
as the disk tier — `force_legacy` is where that swap lands.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

try:
    from . import ccr as _legacy
except ImportError:  # direct unit-test import
    import ccr as _legacy


def _default_dir() -> Path:
    """Resolve the base dir dynamically so patching ``ccr.default_dir`` (the
    test seam) redirects the v2 store too. Single source of truth stays ccr."""
    return _legacy.default_dir()

_BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_MAX_MEM_ITEMS = 512
_LOCK_ERROR_LIMIT = 1  # >1 lock error in a run flips to the legacy disk tier
_HEX12_RE = re.compile(r"^[0-9a-f]{12,16}$")
_ALNUM_RE = re.compile(r"[^0-9A-Za-z]")


def _to_base62(num: int) -> str:
    if num <= 0:
        return "0"
    out = []
    while num:
        num, rem = divmod(num, 62)
        out.append(_BASE62[rem])
    return "".join(reversed(out))


class FastCCRStore:
    """RAM LRU + single SQLite WAL store with Base62 alias refs."""

    def __init__(self, db_dir: Path | None = None, max_mem_items: int = _MAX_MEM_ITEMS) -> None:
        self.base = Path(db_dir) if db_dir else _default_dir()
        self.db_path = self.base / "trimtok_ccr_v2.sqlite3"
        self.max_mem_items = max_mem_items
        self._lock = threading.Lock()
        self._mem: OrderedDict[str, str] = OrderedDict()  # sha12 -> content
        self._alias_mem: dict[str, str] = {}  # alias -> sha12
        self._sha_alias: dict[str, str] = {}  # sha12 -> alias
        self._conn: sqlite3.Connection | None = None
        self._alias_seq = 0
        self._db_errors = 0
        self.force_legacy = False
        self._writable = False
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=5.0
            )
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            # Cache-tier durability: entries are disposable (a corrupt db trips
            # the legacy fallback). Measured: synchronous=OFF gave no gain on
            # this box — commit-bound puts stay ~0.3 ms either way, keep NORMAL.
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA temp_store=MEMORY;")
            cur.execute("PRAGMA busy_timeout=3000;")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                " sha12 TEXT PRIMARY KEY, alias TEXT UNIQUE, content TEXT, created_at REAL)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alias ON chunks(alias);")
            cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
            row = cur.execute("SELECT value FROM meta WHERE key='alias_seq'").fetchone()
            self._alias_seq = int(row[0]) if row and str(row[0]).isdigit() else 0
            self._conn.commit()
            self._writable = True
        except Exception:
            self._db_errors += 1
            self._conn = None
            self._writable = False

    # -- internal helpers --------------------------------------------------

    def _register_db_error(self) -> None:
        self._db_errors += 1
        if self._db_errors > _LOCK_ERROR_LIMIT:
            self.force_legacy = True

    @staticmethod
    def _sha12(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:12]

    def _mem_refresh(self, sha12: str, content: str) -> None:
        self._mem[sha12] = content
        self._mem.move_to_end(sha12)
        while len(self._mem) > self.max_mem_items:
            evicted_sha, _ = self._mem.popitem(last=False)
            evicted_alias = self._sha_alias.pop(evicted_sha, None)
            if evicted_alias:
                self._alias_mem.pop(evicted_alias, None)

    def _legacy_ref(self, content: str) -> str:
        digest = _legacy.put(content, dir=self.base)
        return f"#{digest}" if digest else ""

    def _legacy_get(self, norm: str, ttl_hours: int) -> str | None:
        return _legacy.get(norm, dir=self.base, ttl_hours=ttl_hours)

    # -- public API --------------------------------------------------------

    def put(
        self,
        content: str,
        *,
        ttl_hours: int = 24,
        max_entries: int = 1000,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> str:
        """Store content; return a ref like '#c1' ('' on failure). Never raises."""
        try:
            if not isinstance(content, str):
                return ""
            if self.force_legacy or not self._writable or self._conn is None:
                return self._legacy_ref(content)
            sha12 = self._sha12(content)
            with self._lock:
                known = self._sha_alias.get(sha12)
                if known:
                    self._mem_refresh(sha12, content)
                    return "#" + known
                alias = ""
                for _ in range(3):
                    self._alias_seq += 1
                    alias = "c" + _to_base62(self._alias_seq)
                    try:
                        # Happy path is ONE insert: OR IGNORE absorbs the dup
                        # (sha hit, or a burned alias) without a prior SELECT.
                        cur = self._conn.execute(
                            "INSERT OR IGNORE INTO chunks "
                            "(sha12, alias, content, created_at) VALUES (?,?,?,?)",
                            (sha12, alias, content, time.time()),
                        )
                        if cur.rowcount:
                            # same transaction: alias counter + chunk land together
                            self._conn.execute(
                                "INSERT OR REPLACE INTO meta (key, value) "
                                "VALUES ('alias_seq', ?)",
                                (str(self._alias_seq),),
                            )
                            self._conn.commit()
                            break
                        row = self._conn.execute(
                            "SELECT alias FROM chunks WHERE sha12=?", (sha12,)
                        ).fetchone()
                        if row and row[0]:
                            self._sha_alias[sha12] = row[0]
                            self._alias_mem[row[0]] = sha12
                            self._mem_refresh(sha12, content)
                            return "#" + row[0]
                        alias = ""  # stale counter burned the alias; retry
                        continue
                    except sqlite3.IntegrityError:
                        try:
                            self._conn.rollback()
                        except sqlite3.Error:
                            pass
                        continue
                if not alias:
                    raise sqlite3.OperationalError("alias collision")
                self._sha_alias[sha12] = alias
                self._alias_mem[alias] = sha12
                self._mem_refresh(sha12, content)
                if self._alias_seq % 64 == 0:
                    self._gc(ttl_hours, max_entries, max_bytes)
                return "#" + alias
        except sqlite3.Error:
            with self._lock:
                self._register_db_error()
            return self._legacy_ref(content)
        except Exception:
            return self._legacy_ref(content)

    def get(self, ref: str, *, ttl_hours: int = 24) -> str | None:
        """Fetch content by '#c1' alias or 12-hex digest; None when missing/expired."""
        norm = ""
        try:
            norm = self._normalize(ref)
            if not norm:
                return None
            if self.force_legacy or not self._writable or self._conn is None:
                return self._legacy_get(norm, ttl_hours)
            with self._lock:
                sha12 = self._alias_mem.get(norm)
                if sha12 and sha12 in self._mem:
                    self._mem.move_to_end(sha12)
                    return self._mem[sha12]
                if norm in self._mem:
                    self._mem.move_to_end(norm)
                    return self._mem[norm]
                row = self._conn.execute(
                    "SELECT sha12, content, created_at FROM chunks WHERE alias=? LIMIT 1",
                    (norm,),
                ).fetchone()
                if row is None and _HEX12_RE.match(norm):
                    row = self._conn.execute(
                        "SELECT sha12, content, created_at FROM chunks WHERE sha12=? LIMIT 1",
                        (norm,),
                    ).fetchone()
                if (
                    row is None
                    and len(norm) >= 8
                    and all(c in "0123456789abcdef" for c in norm)
                ):
                    rows = self._conn.execute(
                        "SELECT sha12, content, created_at FROM chunks "
                        "WHERE sha12 LIKE ? LIMIT 2",
                        (norm + "%",),
                    ).fetchall()
                    if len(rows) == 1:
                        row = rows[0]
                if row is not None:
                    sha12, content = row[0], row[1]
                    created_at = float(row[2] or 0.0)
                    if ttl_hours and ttl_hours > 0 and (time.time() - created_at) > ttl_hours * 3600:
                        return None
                    self._mem_refresh(sha12, content)
                    return content
            # miss -> legacy file store (continuity with pre-v2 markers)
            return self._legacy_get(norm, ttl_hours)
        except sqlite3.Error:
            with self._lock:
                self._register_db_error()
            return self._legacy_get(norm, ttl_hours)
        except Exception:
            return None

    def purge(self, *, before_seconds: float = 0) -> int:
        """Drop expired/all entries from both tiers. Returns removed count."""
        purged = 0
        try:
            with self._lock:
                if self._writable and self._conn is not None:
                    if before_seconds <= 0:
                        cur = self._conn.execute("DELETE FROM chunks")
                    else:
                        cur = self._conn.execute(
                            "DELETE FROM chunks WHERE created_at < ?",
                            (time.time() - before_seconds,),
                        )
                    purged += max(0, cur.rowcount)
                    self._conn.commit()
                    self._mem.clear()
                    self._alias_mem.clear()
                    self._sha_alias.clear()
        except sqlite3.Error:
            with self._lock:
                self._register_db_error()
        try:
            purged += _legacy.purge(dir=self.base, before_seconds=before_seconds)
        except Exception:
            pass
        return purged

    def stats(self, *, ttl_hours: int = 24) -> dict:
        """Merged entry/byte counts from both tiers. Never raises."""
        entries = 0
        total = 0
        try:
            with self._lock:
                if self._writable and self._conn is not None:
                    row = self._conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) FROM chunks"
                    ).fetchone()
                    entries += int(row[0] or 0)
                    total += int(row[1] or 0)
        except sqlite3.Error:
            with self._lock:
                self._register_db_error()
        except Exception:
            pass
        try:
            legacy = _legacy.stats(dir=self.base, ttl_hours=ttl_hours)
            entries += int(legacy.get("entries", 0))
            total += int(legacy.get("bytes", 0))
        except Exception:
            pass
        return {"entries": entries, "bytes": total}

    # -- maintenance -------------------------------------------------------

    def _gc(self, ttl_hours: int, max_entries: int, max_bytes: int) -> None:
        try:
            cutoff = time.time() - max(1, ttl_hours) * 3600
            self._conn.execute("DELETE FROM chunks WHERE created_at < ?", (cutoff,))
            count, total = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) FROM chunks"
            ).fetchone()
            count, total = int(count or 0), int(total or 0)
            while count > max_entries:
                cur = self._conn.execute(
                    "DELETE FROM chunks WHERE sha12 IN ("
                    " SELECT sha12 FROM chunks ORDER BY created_at LIMIT ?)",
                    (max(1, count - max_entries),),
                )
                removed = max(0, cur.rowcount)
                count -= removed
                if removed == 0:
                    break
            while total > max_bytes:
                rows = self._conn.execute(
                    "SELECT sha12, COALESCE(LENGTH(content), 0) FROM chunks "
                    "ORDER BY created_at LIMIT 64"
                ).fetchall()
                if not rows:
                    break
                for sha12, size in rows:
                    self._conn.execute("DELETE FROM chunks WHERE sha12=?", (sha12,))
                    total -= int(size or 0)
                    if total <= max_bytes:
                        break
            self._conn.commit()
        except sqlite3.Error:
            self._register_db_error()
        except Exception:
            pass

    def close(self) -> None:
        """Close the DB connection (tests / cleanup). Never raises."""
        try:
            with self._lock:
                if self._conn is not None:
                    self._conn.close()
                    self._conn = None
                    self._writable = False
        except Exception:
            pass

    @staticmethod
    def _normalize(ref: object) -> str:
        s = str(ref or "").strip().strip("<>").strip()
        if "retrieve:" in s:
            s = s.split("retrieve:", 1)[1]
        s = s.split(",")[0].strip()
        if s.startswith("#"):
            s = s[1:]
        return _ALNUM_RE.sub("", s)[:16]


# ---------------------------------------------------------------------------
# Module-level facade (mirrors ccr.py's API shape for the plugin)
# ---------------------------------------------------------------------------

_STORES: dict[str, FastCCRStore] = {}
_STORES_LOCK = threading.Lock()


def _store_for(dir: Path | None = None) -> FastCCRStore:
    key = str(Path(dir) if dir else _default_dir())
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = FastCCRStore(Path(key))
            _STORES[key] = store
        return store


def put(
    content: str,
    *,
    dir: Path | None = None,
    max_entries: int = 1000,
    max_bytes: int = 50 * 1024 * 1024,
    ttl_hours: int = 24,
) -> str:
    """Store content; returns '#c1'-style ref, or '' on any failure."""
    try:
        return _store_for(dir).put(
            content, ttl_hours=ttl_hours, max_entries=max_entries, max_bytes=max_bytes
        )
    except Exception:
        return ""


def get(ref: str, *, dir: Path | None = None, ttl_hours: int = 24) -> str | None:
    """Resolve a ref (alias or hex digest); None when missing/expired."""
    try:
        return _store_for(dir).get(ref, ttl_hours=ttl_hours)
    except Exception:
        return None


def purge(*, dir: Path | None = None, before_seconds: float = 0) -> int:
    try:
        return _store_for(dir).purge(before_seconds=before_seconds)
    except Exception:
        return 0


def stats(*, dir: Path | None = None, ttl_hours: int = 24) -> dict:
    try:
        return _store_for(dir).stats(ttl_hours=ttl_hours)
    except Exception:
        return {"entries": 0, "bytes": 0}


def close(dir: Path | None = None) -> None:
    """Close and drop the store for this dir (used by tests). Never raises."""
    try:
        key = str(Path(dir) if dir else _default_dir())
        with _STORES_LOCK:
            store = _STORES.pop(key, None)
        if store is not None:
            store.close()
    except Exception:
        pass
