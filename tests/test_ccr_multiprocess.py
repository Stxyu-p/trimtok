import unittest
from trimtok.ccr import FastCCRStore

class MultiProcessCCRTests(unittest.TestCase):
    def test_sqlite_lru_does_not_bypass_ttl(self):
        import tempfile, pathlib
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            store = FastCCRStore(db_dir=pathlib.Path(tmp))
            try:
                with mock.patch("trimtok.ccr.time.time", return_value=1_000_000.0):
                    ref = store.put("TTL payload kept in the SQLite LRU")
                self.assertTrue(ref)
                with mock.patch("trimtok.ccr.time.time", return_value=1_000_000.0 + 23 * 3600):
                    self.assertEqual(store.get(ref, ttl_hours=24), "TTL payload kept in the SQLite LRU")
                with mock.patch("trimtok.ccr.time.time", return_value=1_000_000.0 + 25 * 3600):
                    self.assertIsNone(store.get(ref, ttl_hours=24))
            finally:
                store.close()

    def test_closed_sqlite_lru_does_not_bypass_ttl(self):
        import tempfile, pathlib
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            store = FastCCRStore(db_dir=pathlib.Path(tmp))
            with mock.patch("trimtok.ccr.time.time", return_value=1_000_000.0):
                ref = store.put("TTL payload in closed SQLite store")
            self.assertTrue(ref)
            store.close()
            with mock.patch("trimtok.ccr.time.time", return_value=1_000_000.0 + 25 * 3600):
                self.assertIsNone(store.get(ref, ttl_hours=24))

    def test_two_independent_stores_never_collide_on_aliases(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            # Simulates parent Hermes process and spawned Subagent process sharing the same DB dir
            parent_store = FastCCRStore(db_dir=tmp_path)
            subagent_store = FastCCRStore(db_dir=tmp_path)
            try:
                ref_parent = parent_store.put("Parent payload alpha " * 100)
                ref_sub = subagent_store.put("Subagent payload beta " * 100)

                self.assertNotEqual(ref_parent, ref_sub, f"Alias collision across processes: {ref_parent} == {ref_sub}")
                # Parent must be able to read subagent's ref from SQLite, and vice versa
                self.assertEqual(parent_store.get(ref_sub), "Subagent payload beta " * 100)
                self.assertEqual(subagent_store.get(ref_parent), "Parent payload alpha " * 100)
            finally:
                parent_store.close()
                subagent_store.close()

    def test_failed_sqlite_write_never_emits_dangling_in_memory_alias(self):
        import tempfile, pathlib, unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            store = FastCCRStore(db_dir=tmp_path)
            orig_conn = store._conn
            try:
                # Force SQLite cursor to fail
                mock_conn = mock.MagicMock()
                mock_conn.cursor.side_effect = RuntimeError("disk full")
                store._conn = mock_conn
                alias = store.put("Some large chunk that fails db write " * 50)
                self.assertEqual(alias, "", "Must fail-open (empty alias) on DB failure")
            finally:
                store._conn = orig_conn
                store.close()

    def test_ccr_get_alias_skips_legacy_file_glob(self):
        import tempfile, pathlib, unittest.mock as mock
        import trimtok.ccr as ccr_mod
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            # Create a poison legacy file 'c19999.txt' that _file_get('#c1') would wrongly match!
            (tmp_path / "c19999.txt").write_text("POISON_LEGACY_FILE", encoding="utf-8")
            store = ccr_mod._get_store(tmp_path)
            try:
                ref = store.put("Canonical SQLite Payload " * 40)
                self.assertEqual(ref, "#c1")
                # ccr.get('#c1') must return the SQLite payload, NEVER the poison c1*.txt file
                with mock.patch.object(ccr_mod, "_file_get", wraps=ccr_mod._file_get) as spy_file_get:
                    val = ccr_mod.get("#c1", dir=tmp_path)
                    self.assertEqual(val, "Canonical SQLite Payload " * 40)
                    self.assertEqual(spy_file_get.call_count, 0)
            finally:
                ccr_mod.close(tmp_path)

if __name__ == "__main__":
    unittest.main()
