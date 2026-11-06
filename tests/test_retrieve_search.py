"""The MCP-facing search path: a schema-valid client must be able to omit hash."""
import json

from trimtok import RETRIEVE_SCHEMA, _handle_retrieve, ccr


def test_schema_accepts_query_without_hash():
    assert "required" not in RETRIEVE_SCHEMA["parameters"]
    assert {"required": ["query"]} in RETRIEVE_SCHEMA["parameters"]["anyOf"]


def test_handle_retrieve_searches_by_query_alone():
    ref = ccr.put("unique_search_marker_for_retrieve")
    assert ref

    payload = json.loads(_handle_retrieve({"query": "unique_search_marker_for_retrieve"}))
    assert payload["count"] == 1
    assert payload["hits"][0]["alias"] == ref


def test_handle_retrieve_prefers_hash_when_both_are_given():
    ref = ccr.put("hash_wins_marker")
    assert ref

    payload = json.loads(_handle_retrieve({"hash": ref, "query": "hash_wins_marker"}))
    assert "hits" not in payload
    assert "hash_wins_marker" in json.dumps(payload)


def test_handle_retrieve_requires_one_of_hash_or_query():
    payload = json.loads(_handle_retrieve({}))
    assert "error" in payload
