from pathlib import Path

from trimtok import ccr

def test_ccr_fts5_indexing_and_search(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    content1 = "def authenticate_user(token):\n    if not token:\n        raise ValueError('invalid token')\n"
    content2 = "def render_dashboard(data):\n    return f'Dashboard: {data}'\n"
    
    alias1 = store.put(content1)
    alias2 = store.put(content2)
    
    hits = store.search("authenticate_user")
    assert [h["alias"] for h in hits] == [alias1]
    assert "authenticate_user" in hits[0]["snippet"]

    assert [h["alias"] for h in store.search("dashboard")] == [alias2]
    assert store.search("non_existent_symbol_xyz") == []

def test_ccr_fts5_sanitization_keeps_the_match(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    store.put("error in function foo: code 500")
    # FTS5 operators and unclosed quotes must not raise, and the useful token
    # must still match -- a sanitizer that swallows the whole query is a bug.
    assert len(store.search('*** foo ***')) == 1
    assert len(store.search('foo"')) == 1
    # A query made only of operators matches nothing, and must not raise.
    assert store.search("*** OR AND") == []

def test_ccr_fts5_searches_non_latin_scripts(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    store.put("连接被拒绝 ConnectionRefused Ошибка")
    assert len(store.search("ConnectionRefused")) == 1
    assert len(store.search("连接被拒绝")) == 1
    assert len(store.search("Ошибка")) == 1

def test_ccr_fts5_sync_on_purge(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    store.put("unique_to_purge_payload")
    assert len(store.search("unique_to_purge_payload")) == 1

    store.purge()
    assert store.search("unique_to_purge_payload") == []

def test_ccr_fts5_backfills_a_pre_existing_store(tmp_path: Path):
    """A store written before the FTS table existed must still be searchable."""
    store = ccr.FastCCRStore(tmp_path)
    alias = store.put("legacy_payload_marker_xyz")
    # Simulate a 5.4.1 database: chunks present, index never built.
    assert store._conn is not None
    store._conn.execute("DELETE FROM chunks_fts")
    store._conn.commit()
    store.close()

    reopened = ccr.FastCCRStore(tmp_path)
    assert [h["alias"] for h in reopened.search("legacy_payload_marker_xyz")] == [alias]

def test_ccr_search_skips_expired_chunks(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    alias = store.put("expiring_payload_marker")
    assert store._conn is not None
    store._conn.execute("UPDATE chunks SET created_at = 0 WHERE alias = ?", (alias,))
    store._conn.commit()

    assert store.search("expiring_payload_marker", ttl_hours=1) == []

    store._conn.execute("UPDATE chunks SET pinned = 1 WHERE alias = ?", (alias,))
    store._conn.commit()
    assert len(store.search("expiring_payload_marker", ttl_hours=1)) == 1

def test_ccr_stats_count_the_index(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    store.put("payload " * 500)

    s = store.stats()
    assert s["entries"] == 1
    assert s["fts_bytes"] > 0
    assert s["bytes"] == s["payload_bytes"] + s["fts_bytes"]
