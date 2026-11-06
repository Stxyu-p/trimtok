"""CCR store invariants that the bug hunt showed were only assumed.

Each test fails on the pre-fix code; together they pin the two guarantees the
store advertises: never emit a marker that cannot resolve, and never lose
stored bytes.
"""
import tempfile
import time
from pathlib import Path

import trimtok.ccr as ccr
from trimtok.ccr import FastCCRStore


def _store() -> FastCCRStore:
    return FastCCRStore(Path(tempfile.mkdtemp()))


def test_ttl_sweep_drops_the_ram_entry_so_no_dead_alias_is_reissued():
    store = _store()
    store.put("payload one", ttl_hours=0)
    for i in range(48):
        store.put(f"filler {i}", ttl_hours=0)
    assert store._conn is not None
    store._conn.execute("UPDATE chunks SET created_at = 0")
    store._conn.commit()
    store.put("trigger", ttl_hours=1)          # seq 50 runs the sweep

    alias = store.put("payload one", ttl_hours=1)

    # The alias must be backed by a row, or it dies at the next process start.
    assert store._conn is not None
    rows = store._conn.execute("SELECT COUNT(*) FROM chunks WHERE alias = ?", (alias,)).fetchone()[0]
    assert rows == 1


def test_put_survives_a_ram_entry_removed_by_unpin():
    store = _store()
    ref = store.put("payload A")
    store.pin(ref)
    store.unpin(ref)

    again = store.put("payload A")

    assert again == ref
    assert store.get(again) == "payload A"


def test_file_backend_treats_zero_ttl_as_no_expiry():
    base = Path(tempfile.mkdtemp())
    ref = ccr.put("file payload", dir=base)

    assert ccr.get(ref, dir=base, ttl_hours=0) == "file payload"
    assert ccr.stats(dir=base, ttl_hours=0)["entries"] == 1


def test_stats_and_evict_count_bytes_not_characters():
    store = _store()
    payload = "上下文压缩测试" * 10
    store.put(payload)

    stats = store.stats()

    assert stats["bytes"] == len(payload.encode()) * 2  # payload + FTS copy
    assert store.evict_to_size(200) == 1
    assert store.stats()["entries"] == 0
