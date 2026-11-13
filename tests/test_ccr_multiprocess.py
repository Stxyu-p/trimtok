import unittest
from trimtok.ccr import FastCCRStore

class MultiProcessCCRTests(unittest.TestCase):
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

    def test_write_lock_contention_fails_open_instead_of_stalling_the_hook(self):
        import tempfile, pathlib, time
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            holder = FastCCRStore(db_dir=tmp_path)
            writer = FastCCRStore(db_dir=tmp_path)
            try:
                # Another process mid-commit holds the write lock.
                assert holder._conn is not None
                holder._conn.execute("BEGIN IMMEDIATE")
                started = time.perf_counter()
                ref = writer.put("payload blocked behind a writer " * 100)
                elapsed = time.perf_counter() - started
                self.assertEqual(ref, "", "must fail open, never emit a dangling alias")
                # The old 10s busy_timeout plus a 3-step retry ladder held the hook
                # for 33s; a stall here blocks a whole agent turn.
                self.assertLess(elapsed, 3.0, f"put() stalled {elapsed:.2f}s behind a write lock")
            finally:
                try:
                    assert holder._conn is not None
                    holder._conn.execute("ROLLBACK")
                except Exception:
                    pass
                holder.close()
                writer.close()

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
