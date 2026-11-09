"""JSON envelope folding: wrapper objects must yield real, lossless savings."""

import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="trimtok-test-envelope-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trimtok.json_envelope import fold_envelope_array, fold_envelope_text


def _wrap(text):
    return {"output": text, "ok": True}


LOG = "\n".join(f"2026-11-09T10:00:{i:02d} INFO step {i} finished cleanly" for i in range(120))


class TestFoldEnvelopeText(unittest.TestCase):
    def test_compresses_dominant_string_and_keeps_siblings(self):
        payload = json.dumps(_wrap(LOG), ensure_ascii=False)
        result = fold_envelope_text(payload, lambda t: t[: len(t) // 4])
        self.assertIsNotNone(result)
        candidate, meta = result
        self.assertLess(len(candidate), len(payload))
        restored = json.loads(candidate)
        self.assertTrue(restored["ok"])
        self.assertEqual(meta["envelope_path"], "output")
        self.assertEqual(restored["output"], LOG[: len(LOG) // 4])

    def test_skips_when_inner_does_not_shrink(self):
        payload = json.dumps(_wrap(LOG))
        self.assertIsNone(fold_envelope_text(payload, lambda t: t))

    def test_skips_non_json_and_arrays(self):
        self.assertIsNone(fold_envelope_text("not json", lambda t: "x"))
        self.assertIsNone(fold_envelope_text(json.dumps([{"a": 1}]), lambda t: "x"))

    def test_skips_when_no_string_is_big_enough(self):
        payload = json.dumps({"output": "short", "ok": False})
        self.assertIsNone(fold_envelope_text(payload, lambda t: "x"))

    def test_compressor_errors_fail_open(self):
        payload = json.dumps(_wrap(LOG))

        def boom(_text):
            raise RuntimeError("nope")

        self.assertIsNone(fold_envelope_text(payload, boom))


class TestFoldEnvelopeArray(unittest.TestCase):
    def test_uniform_rows_bucket_into_table(self):
        rows = [{"id": i, "name": f"svc-{i}", "ok": i % 2 == 0} for i in range(40)]
        payload = json.dumps({"total": 40, "items": rows})
        result = fold_envelope_array(payload)
        self.assertIsNotNone(result)
        candidate, meta = result
        self.assertLess(len(candidate), len(payload))
        self.assertEqual(meta["envelope_path"], "items")
        self.assertEqual(meta["envelope_rows"], 40)
        self.assertIn("@envelope:", candidate)
        self.assertIn('"total":40', candidate)

    def test_mixed_schema_rows_are_bucketed_not_rejected(self):
        rows = [{"id": i, "name": f"x{i}"} if i % 3 else {"id": i, "extra": True} for i in range(30)]
        payload = json.dumps({"items": rows})
        result = fold_envelope_array(payload)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result[0].count("##bucket"), 2)

    def test_skips_small_or_unsupported(self):
        self.assertIsNone(fold_envelope_array(json.dumps({"items": [{"a": 1}]})))
        self.assertIsNone(fold_envelope_array(json.dumps([{"a": 1}])))
        self.assertIsNone(fold_envelope_array("nope"))


if __name__ == "__main__":
    unittest.main()
