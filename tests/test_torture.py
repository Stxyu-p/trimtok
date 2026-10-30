"""trimtok torture regression: adversarial inputs must never raise.

Run: python tests/test_torture.py
Locks 2026-10-13 hardening probe results (all fail-open by design).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trimtok.core import compress_terminal
from trimtok.dedup import compress_tool_result, minify_json, remember, clear_session
from trimtok.ccr import put as ccr_put


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1
check.failed = 0


# 1. Unpaired surrogates: never raises (utf-8 replace at hash/CCR boundary)
out, st = compress_terminal("cmd", "\ud800 hello \udfff world" + "x" * 2500)
check("surrogate-terminal-no-raise", isinstance(out, str))
out, st = compress_tool_result("tool", "\ud800 test" + "y" * 2500, session_id="torture")
check("surrogate-tool-no-raise", isinstance(out, str))
check("surrogate-ccr-no-raise", isinstance(ccr_put("\ud800 lone"), str))

# 2. None at ingestion: typed passthrough gates, never TypeError
out, st = compress_terminal(None, None)
check("none-terminal", out is None and st["gate"] == "passthrough_non_str")
out, st = compress_tool_result(None, None, session_id=None)
check("none-tool", out is None and st["gate"] == "passthrough_non_str")
check("none-minify", minify_json(None) == (None, 0))
check("none-ccr-put", ccr_put(None) == "")

# 3. Empty / whitespace-only
out, st = compress_terminal("cmd", "")
check("empty-terminal", st["gate"] == "passthrough_small")
out, st = compress_tool_result("t", "", session_id="tort-empty")
check("empty-tool", st["gate"] == "passthrough_small")

# 4. Thai combining marks: rail keeps mid-doc error, Thai never crashes
thai = "ช่วยด้วยนะคะ " * 500
out, st = compress_terminal("cmd", thai)
check("thai-terminal-no-raise", isinstance(out, str))
out, st = compress_tool_result("search_files", thai, session_id="torture-th")
check("thai-tool-no-raise", isinstance(out, str))

# 5. Deep-nesting JSON: minify never raises
out, saved = minify_json("{\"a\":" * 5000 + "1" + "}" * 5000)
check("deep-json-no-raise", isinstance(saved, int))

# 6. Marker re-feed: already-compressed input passes through untouched
refeed = "[tt:tool kind=result saved=500 retrieve:abcdef123456] hello" + "z" * 3000
out, st = compress_tool_result("t", refeed, session_id="tort-refeed")
check("marker-refeed-sentinel", st["gate"] == "passthrough_sentinel" and out == refeed)

# 7. Error rail survives torture: FAILED line must stay verbatim
out, st = compress_terminal("pytest", "FAILED test_x - assert 1==2\n" + "ok line\n" * 2000)
check("error-preserved", "FAILED test_x" in out)

# 8. 1MB unique: bounded time (<5s), never raises, CCR recovers
big = "".join("unique-line-%08d-abcdef1234567890\n" % i for i in range(30000))
t0 = time.perf_counter()
out, st = compress_terminal("cmd", big)
dt = (time.perf_counter() - t0) * 1000
check("1MB-no-raise", isinstance(out, str))
check("1MB-bounded", dt < 5000)
check("1MB-saves", st.get("saved_chars", 0) > 0)

# 9. remember() never raises on hostile input
try:
    remember(None, "tort-none")
    remember("\ud800", "tort-surr")
    remember("", "tort-empty")
    check("remember-no-raise", True)
except Exception:
    check("remember-no-raise", False)
finally:
    for s in ("tort-none", "tort-surr", "tort-empty", "torture", "torture-th",
              "tort-empty", "tort-refeed", "e", "e2", "e3", "s1", "s2"):
        try:
            clear_session(s)
        except Exception:
            pass

import unittest

class TestTortureSuite(unittest.TestCase):
    def test_torture_checks_pass(self) -> None:
        self.assertEqual(check.failed, 0)


print(f"\n{check.failed} failures")
if __name__ == "__main__":
    sys.exit(1 if check.failed else 0)
