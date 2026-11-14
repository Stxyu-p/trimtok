"""Telemetry must describe delivered output, not only attempted transforms."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trimtok
from trimtok import mcp_server


class _Context:
    @staticmethod
    def get_config(_key, default):
        return default


class TestTelemetryDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.usage_path = Path(self.temp.name) / "usage.jsonl"
        self.audit_path = Path(self.temp.name) / "audit.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def _rows(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_hook_ccr_failure_is_logged_as_unchanged_output(self):
        raw = "original payload " * 100
        stats = {
            "gate": "ok", "before_chars": len(raw), "after_chars": 100,
            "saved_chars": len(raw) - 100, "digest": "d" * 12,
            "kind": "json", "ms": 1.0, "lossless_tier0": False,
        }
        trimtok._on_tool_result._ctx = _Context()
        with patch.object(trimtok, "_usage_path", return_value=self.usage_path), \
             patch.object(trimtok, "compress_tool_result", return_value=("compressed", stats)), \
             patch.object(trimtok, "_store_original", return_value=None):
            result = trimtok._on_tool_result(tool_name="read_file", result=raw)
        row = self._rows(self.usage_path)[0]
        self.assertIsNone(result)
        self.assertEqual(row["gate"], "passthrough_storage_failure")
        self.assertEqual(row["before"], row["after"])
        self.assertEqual(row["saved"], 0)

    def test_mcp_ccr_failure_is_not_a_successful_saving(self):
        raw = "original payload " * 100
        stats = {
            "gate": "ok", "before_chars": len(raw), "after_chars": 100,
            "saved_chars": len(raw) - 100, "digest": "d" * 12,
            "kind": "json", "ms": 1.0, "lossless_tier0": False,
        }
        with patch.object(trimtok, "_usage_path", return_value=self.usage_path), \
             patch.object(trimtok, "_audit_path", return_value=self.audit_path), \
             patch.object(mcp_server, "compress_tool_result", return_value=("compressed", stats)), \
             patch.object(mcp_server.ccr, "put", return_value=""):
            result = mcp_server._tool_compress_tool({"result": raw, "tool_name": "read_file"})
        row = self._rows(self.usage_path)[0]
        self.assertEqual(result["compressed"], raw)
        self.assertEqual(row["gate"], "passthrough_storage_failure")
        self.assertEqual(row["before"], row["after"])
        self.assertEqual(row["saved"], 0)
        self.assertIsInstance(row["ts"], int)
        self.assertEqual(row["version"], trimtok.VERSION)
        self.assertFalse(self.audit_path.exists())

    def test_usage_write_failure_is_logged_but_fail_open(self):
        with patch.object(trimtok, "_usage_path", side_effect=OSError("disk unavailable")):
            with self.assertLogs("trimtok", level="WARNING") as logs:
                trimtok._usage({"hook": "tool"})
        self.assertIn("usage telemetry write failed", "\\n".join(logs.output))

    def test_malformed_usage_rows_are_logged_and_skipped(self):
        self.usage_path.write_text("{invalid json}\\n", encoding="utf-8")
        with patch.object(trimtok, "_usage_path", return_value=self.usage_path):
            with self.assertLogs("trimtok", level="WARNING") as logs:
                rows = list(trimtok._iter_usage_rows())
        self.assertEqual(rows, [])
        self.assertIn("usage row parse failed", "\\n".join(logs.output))

    def test_usage_records_template_savings_and_build_metadata(self):
        with patch.object(trimtok, "_usage_path", return_value=self.usage_path):
            trimtok._usage_tool("read_file", "payload", {
                "gate": "ok", "before_chars": 7, "after_chars": 5,
                "saved_chars": 2, "intra_template_saved": 3,
            }, _Context())
        row = self._rows(self.usage_path)[0]
        self.assertEqual(row["intra_template_saved"], 3)
        self.assertEqual(row["saved"], row["before"] - row["after"])
        self.assertEqual(row["version"], trimtok.VERSION)
        self.assertIsInstance(row["ts"], int)

    def test_usage_tool_terminal_captures_cmd_for_trace_mining(self):
        with patch.object(trimtok, "_usage_path", return_value=self.usage_path):
            trimtok._usage_tool(
                "terminal",
                "ok",
                {"gate": "ok", "before_chars": 2, "after_chars": 2, "saved_chars": 0},
                _Context(),
                args={"command": "python -m unittest discover -s tests"},
            )
        row = self._rows(self.usage_path)[0]
        self.assertEqual(row["cmd"], "python -m unittest discover -s tests")


if __name__ == "__main__":
    unittest.main()
