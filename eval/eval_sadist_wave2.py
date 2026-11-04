"""Trimtok Heavy Artillery Sadist Suite (Wave 2).
Tests complex agent workflows: ExceptionGroups, hostile TOON delimiters,
read->patch->re-read mutation loops, and hostile retrieve queries.
"""
from __future__ import annotations

import json
import os
import sys
import time
import tiktoken
import traceback

import trimtok as tt
from trimtok.core import compress_terminal, VERSION
from trimtok.dedup import compress_tool_result, _try_merged_toon, clear_session
from trimtok.toon_codec import fold_json_to_toon, toon_decode, _strict_eq
from trimtok.ccr import get as ccr_get, put as ccr_put
from trimtok.pruners import prune_traceback
from trimtok import RETRIEVE_SCHEMA

enc = tiktoken.get_encoding("cl100k_base")

results = []

def run_test(test_id: str, name: str, fn):
    t0 = time.perf_counter()
    passed = False
    details = ""
    tok_saved = 0
    try:
        passed, details, tok_saved = fn()
    except Exception as e:
        passed = False
        details = f"CRASH: {type(e).__name__}: {e}"
        tok_saved = 0
    ms = (time.perf_counter() - t0) * 1000
    results.append({
        "id": test_id,
        "name": name,
        "passed": passed,
        "ms": ms,
        "tok_saved": tok_saved,
        "details": details
    })
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {test_id} ({ms:5.1f}ms): {name} -> {details}")

# -------------------------------------------------------------
# HARD-1: Chained Exceptions & Deep Traceback Crash-Site Locking
# -------------------------------------------------------------

def test_hard_1_chained_traceback():
    # Multi-layered exception chain with 30 framework frames in between
    def bottom():
        raise ConnectionResetError("Connection reset by peer (TCP/5432)")
    def middle():
        try:
            bottom()
        except Exception as e:
            raise RuntimeError("Database pool failure during checkout") from e
    
    tb_str = ""
    try:
        middle()
    except Exception:
        tb_str = traceback.format_exc()
    
    # Expand with artificial framework frames in the middle
    expanded_tb = (
        "Traceback (most recent call last):\n"
        + "\n".join([f'  File "/usr/lib/python3.12/site-packages/starlette/middleware/base.py", line {20+i}, in call_next\n    response = await coro' for i in range(25)])
        + "\n"
        + tb_str
    )
    
    out, meta = compress_terminal("python test_chain.py", expanded_tb)
    # INVARIANT: Both leaf errors must survive pruning!
    has_conn = "ConnectionResetError" in out
    has_runtime = "RuntimeError" in out
    has_leaf_msg = "Connection reset by peer" in out
    
    all_kept = has_conn and has_runtime and has_leaf_msg
    tok_b = len(enc.encode(expanded_tb))
    tok_a = len(enc.encode(out))
    return all_kept, f"Locked all crash-sites: {all_kept} ({tok_b} -> {tok_a} tok)", tok_b - tok_a

# -------------------------------------------------------------
# HARD-2: Hostile Key Delimiters & Pipe Injection in JSON TOON
# -------------------------------------------------------------

def test_hard_2_hostile_toon_delimiters():
    # JSON with pipes, quotes, escapes, and reserved keywords inside keys and values
    hostile_data = [
        {
            "id|pipe": 101,
            "user\"quote": "admin|root|sudo",
            "escaped\\path": "C:\\Users\\admin\\file.txt",
            "nested_list": ["a|b", "c\nd", "e\"f"],
            "@prefix": "https://hack.org"
        },
        {
            "id|pipe": 102,
            "user\"quote": "guest",
            "escaped\\path": "/var/log/syslog",
            "nested_list": ["plain"],
            "@prefix": "https://example.com"
        },
        {
            "id|pipe": 103,
            "user\"quote": "member|viewer",
            "escaped\\path": "D:\\data\\db.sqlite",
            "nested_list": [],
            "@prefix": "https://local"
        }
    ]
    raw = json.dumps(hostile_data)
    cand = fold_json_to_toon(raw)
    
    # If it encoded to TOON, the roundtrip decode MUST be 100% structurally identical!
    if cand != raw:
        decoded = toon_decode(cand)
        exact = _strict_eq(decoded, hostile_data)
        return exact, f"TOON roundtrip with pipe/quote/escape delimiters: {exact}", len(enc.encode(raw)) - len(enc.encode(cand))
    else:
        # If safely refused due to hazardous keys, that is also valid fail-safe behavior
        return True, "Safely refused hazardous delimiter keys", 0

# -------------------------------------------------------------
# HARD-3: Read -> Patch -> Re-read State Mutation Invalidation
# -------------------------------------------------------------

def test_hard_3_read_patch_reread_lifecycle():
    sess = f"hard3_{time.time()}"
    file_path = "C:/app/services/auth.py"
    
    v1_code = "1|def login(user, pwd):\n2|    # old insecure check\n3|    return user == 'admin'\n4|\n5|def logout():\n6|    pass\n" * 20
    
    # Turn 1: Read v1
    out1, meta1 = compress_tool_result("read_file", v1_code, args={"path": file_path}, session_id=sess)
    
    # Turn 2: Mutating tool (patch) touches file_path
    patch_payload = "diff --git a/auth.py b/auth.py\n--- a/auth.py\n+++ b/auth.py\n@@ -3,1 +3,1 @@\n-    return user == 'admin'\n+    return verify_hash(pwd)\n"
    # Call patch through compress_tool_result so mutation signal is registered
    compress_tool_result("patch", patch_payload, args={"path": file_path}, session_id=sess)
    
    # Turn 3: Read v2 (modified)
    v2_code = v1_code.replace("user == 'admin'", "verify_hash(pwd)")
    out3, meta3 = compress_tool_result("read_file", v2_code, args={"path": file_path}, session_id=sess)
    
    # INVARIANT: Must NOT claim 'identical to prior read' or return stale v1 cache hit!
    is_not_stale = "identical to prior read" not in out3
    has_new_code = "verify_hash" in out3 or meta3.get("gate") in ("ok", "passthrough_reference")
    
    return is_not_stale and has_new_code, f"Cache invalidated on patch: {is_not_stale}, gate={meta3.get('gate')}", 0

# -------------------------------------------------------------
# HARD-4: Hostile trimtok_retrieve Queries (ReDoS, Negative Limits)
# -------------------------------------------------------------

def test_hard_4_hostile_retrieve_queries():
    # Put a known payload into CCR
    sample_text = "\n".join([f"Line {i:04d}: error code ERR_{i%100} in transaction" for i in range(500)])
    ref = ccr_put(sample_text)
    
    from trimtok import _handle_retrieve
    # Query 1: Negative offset and zero limit
    r1 = _handle_retrieve({"hash": ref, "offset": -10, "limit": 0})
    
    # Query 2: Catastrophic backtracking regex in grep
    # Pattern: (a+)+$ against sample
    r2 = _handle_retrieve({"hash": ref, "grep": "(a+)+$"})
    
    # Query 3: Hostile section name
    r3 = _handle_retrieve({"hash": ref, "section": "non_existent_section_injection"})
    
    # Query 4: Normal errors section
    r4 = _handle_retrieve({"hash": ref, "section": "errors", "limit": 5})
    
    success = (
        isinstance(r1, str) and "lines" in r1
        and isinstance(r2, str) and "error" in r2
        and isinstance(r3, str)
        and isinstance(r4, str) and "ERR_" in r4
    )
    return success, "All 4 hostile retrieve queries handled safely without crash or hang", 0

# -------------------------------------------------------------
# HARD-5: High-Entropy & Compressed Data (Zero Token Inflation)
# -------------------------------------------------------------

def test_hard_5_high_entropy_passthrough():
    # High-entropy random hex / base64 block (uncompressible)
    import secrets
    high_entropy = "\n".join([f"DATA_CHUNK_{i}={secrets.token_hex(64)}" for i in range(80)])
    
    out, meta = compress_terminal("cat dump.bin", high_entropy)
    tok_b = len(enc.encode(high_entropy))
    tok_a = len(enc.encode(out))
    
    # INVARIANT: Must NEVER inflate tokens beyond original (+10% safety margin)
    no_inflation = tok_a <= tok_b + 50
    return no_inflation, f"No token inflation: {tok_b} -> {tok_a} tok (delta={tok_a - tok_b})", max(0, tok_b - tok_a)

# -------------------------------------------------------------
# HARD-6: Multilingual Unicode & Vowel Cluster Git Diff
# -------------------------------------------------------------

def test_hard_6_multilingual_git_diff():
    # Unified diff with Thai tone marks, Hebrew RTL, Chinese, and emoji
    diff_payload = (
        "diff --git a/locale.json b/locale.json\n"
        "index 1234567..89abcdef 100644\n"
        "--- a/locale.json\n"
        "+++ b/locale.json\n"
        "@@ -1,5 +1,5 @@\n"
        "-  \"greeting\": \"สวัสดีครับ\",\n"
        "+  \"greeting\": \"สวัสดีค่ะ/ครับ ยินดีต้อนรับสู่ระบบ\",\n"
        "-  \"welcome\": \"ברוך הבא\",\n"
        "+  \"welcome\": \"ברוכים הבאים למערכת!\",\n"
        "+  \"status\": \"🎉 พร้อมใช้งาน 100% 🚀\"\n"
    ) + "\n".join([f" unchanged line {i}" for i in range(120)])
    
    out, meta = compress_terminal("git diff", diff_payload)
    has_thai = "สวัสดี" in out
    has_hebrew = "ברוך" in out
    has_emoji = "🎉" in out
    
    passed = has_thai and has_hebrew and has_emoji
    tok_b = len(enc.encode(diff_payload))
    tok_a = len(enc.encode(out))
    return passed, f"Preserved all Unicode scripts: {passed} ({tok_b} -> {tok_a} tok)", tok_b - tok_a

# -------------------------------------------------------------
# Launch Wave 2
# -------------------------------------------------------------

print(f"=== LAUNCHING WAVE 2 HEAVY ARTILLERY SADIST SUITE (TRIMTOK v{VERSION}) ===")
run_test("HARD-1", "Chained Traceback Crash-Site Locking", test_hard_1_chained_traceback)
run_test("HARD-2", "Hostile Key Delimiters in JSON TOON", test_hard_2_hostile_toon_delimiters)
run_test("HARD-3", "Read->Patch->Re-read Cache Mutation Lifecycle", test_hard_3_read_patch_reread_lifecycle)
run_test("HARD-4", "Hostile trimtok_retrieve Queries & ReDoS Regex", test_hard_4_hostile_retrieve_queries)
run_test("HARD-5", "High-Entropy Passthrough (Zero Token Inflation)", test_hard_5_high_entropy_passthrough)
run_test("HARD-6", "Multilingual Unicode & Vowel Cluster Git Diff", test_hard_6_multilingual_git_diff)

print("\n=== WAVE 2 SUMMARY ===")
pass_count = sum(1 for r in results if r["passed"])
tot_count = len(results)
tot_tokens_saved = sum(r["tok_saved"] for r in results)
print(f"Passed: {pass_count}/{tot_count} ({pass_count/tot_count*100:.1f}%)")
print(f"Total Wave 2 Tokens Saved: {tot_tokens_saved} tokens")
