import pytest
from pathlib import Path
from trimtok import ccr

def test_ccr_fts5_indexing_and_search(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    content1 = "def authenticate_user(token):\n    if not token:\n        raise ValueError('invalid token')\n"
    content2 = "def render_dashboard(data):\n    return f'Dashboard: {data}'\n"
    
    alias1 = store.put(content1)
    alias2 = store.put(content2)
    
    # Search for specific term in content1
    hits = store.search("authenticate_user")
    assert len(hits) >= 1
    assert hits[0]["alias"] == alias1
    assert "authenticate_user" in hits[0]["snippet"]
    
    # Search for term in content2
    hits2 = store.search("dashboard")
    assert len(hits2) >= 1
    assert hits2[0]["alias"] == alias2
    
    # Search for non-existent term
    assert store.search("non_existent_symbol_xyz") == []

def test_ccr_fts5_sanitization_on_special_chars(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    store.put("error in function foo: code 500")
    # Unclosed quotes or syntax chars should not raise
    hits = store.search('foo" AND OR ***')
    assert isinstance(hits, list)

def test_ccr_fts5_sync_on_purge(tmp_path: Path):
    store = ccr.FastCCRStore(tmp_path)
    store.put("unique_to_purge_payload")
    assert len(store.search("unique_to_purge_payload")) >= 1
    
    # Purge store
    store.purge()
    assert store.search("unique_to_purge_payload") == []
