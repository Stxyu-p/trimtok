"""reverted_gate must say why, and keep the digest so the payload can be replayed."""

import os
import tempfile
import unittest
from unittest import mock

import atexit as _atexit, shutil as _shutil
_OWN_TMP_DIR = tempfile.mkdtemp(prefix="trimtok-test-revert-") if "TRIMTOK_DATA_DIR" not in os.environ else None
if _OWN_TMP_DIR:
    os.environ["TRIMTOK_DATA_DIR"] = _OWN_TMP_DIR
    def _cleanup_tmp():
        try:
            from trimtok import ccr as _ccr
            _ccr.close()
        except Exception:
            pass
        _shutil.rmtree(_OWN_TMP_DIR, ignore_errors=True)
    _atexit.register(_cleanup_tmp)

from trimtok.core import _revert_reason, compress_terminal

REPEATED = "\n".join(f"step {i:03d} finished with no unusual output" for i in range(120))


def _huge_header(*_a, **_kw):
    return ["h" * 4000]





class TestRevertedGateObservability(unittest.TestCase):
    def test_revert_records_reason_and_replayable_digest(self) -> None:
        # A header bigger than the payload guarantees the gain check fails.
        with mock.patch("trimtok.core.adaptive_terminal_head", side_effect=_huge_header), \
             mock.patch("trimtok.core.is_important", return_value=False), \
             mock.patch("trimtok.core.is_exempt", return_value=False), \
             mock.patch("trimtok.core.estimate_tokens", return_value=1):
            out, stats = compress_terminal("make build", REPEATED)

        self.assertEqual(stats["gate"], "reverted_gate")
        self.assertEqual(stats["reason"], "header_cost")
        self.assertTrue(stats.get("digest"))
        self.assertGreater(stats["omitted_lines"], 0)
        self.assertEqual(out, REPEATED)

    def test_no_fold_reason_when_nothing_was_removed(self) -> None:
        self.assertEqual(_revert_reason(0), "no_fold")
        self.assertEqual(_revert_reason(7), "header_cost")


if __name__ == "__main__":
    unittest.main()
