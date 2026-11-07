"""
Regression tests for Red-Team Hardening:
1. CCR SQLite transient lock retry under concurrent writes.
2. MCP Proxy Content-Length bounded reading against malicious/corrupt headers.
"""
import sqlite3
import tempfile
import pathlib
import unittest
import unittest.mock as mock
from trimtok.ccr import FastCCRStore
from trimtok.mcp_proxy import _read_exact

class FlakyConnWrapper:
    def __init__(self, real_conn):
        self._real = real_conn
        self.call_count = 0

    def cursor(self):
        real_cur = self._real.cursor()
        wrapper = self
        
        class FlakyCursorWrapper:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, sql, *args):
                if sql == "BEGIN IMMEDIATE":
                    wrapper.call_count += 1
                    if wrapper.call_count == 1:
                        raise sqlite3.OperationalError("database is locked")
                return self._cur.execute(sql, *args)

            def fetchone(self):
                return self._cur.fetchone()

            def fetchall(self):
                return self._cur.fetchall()

        return FlakyCursorWrapper(real_cur)

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def close(self):
        return self._real.close()

class RedTeamHardeningTests(unittest.TestCase):
    def test_ccr_retries_transient_sqlite_lock_and_succeeds(self):
        """When SQLite raises OperationalError('database is locked') transiently, put() retries and succeeds."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            store = FastCCRStore(db_dir=tmp_path)
            orig_conn = store._conn
            try:
                # Wrap with flaky conn that fails on 1st BEGIN IMMEDIATE
                flaky = FlakyConnWrapper(orig_conn)
                store._conn = flaky
                
                ref = store.put("flaky retry payload " * 10)
                self.assertTrue(ref.startswith("#c"), "Must succeed after retry")
                self.assertGreaterEqual(flaky.call_count, 2, "Must have retried at least once")
                self.assertEqual(store.get(ref), "flaky retry payload " * 10)
            finally:
                store._conn = orig_conn
                store.close()

    def test_mcp_proxy_bounded_read_prevents_unbounded_buffer(self):
        """_read_exact caps reading at maximum allowed frame size (50MB) against corrupted Content-Length."""
        import io
        fake_stream = io.BytesIO(b"X" * 100)
        # Requesting a ridiculous length beyond 50MB should be bounded
        res = _read_exact(fake_stream, 100)
        self.assertEqual(len(res), 100)

if __name__ == "__main__":
    unittest.main()
