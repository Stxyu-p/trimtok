"""v5.3.4 lane tests: JSON intake for read_file, wide/long TOON cells, intent retrieval."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="trimtok-v534-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trimtok import _handle_retrieve  # noqa: E402
from trimtok.__main__ import main as cli_main  # noqa: E402
from trimtok.ccr import put as ccr_put  # noqa: E402
from trimtok.dedup import compress_tool_result  # noqa: E402
from trimtok.toon_codec import _strict_eq, fold_json_to_toon, toon_decode  # noqa: E402


class TestToonWideAndLongCells(unittest.TestCase):
    def test_wide_uniform_table_roundtrips(self):
        rows = [{f"f_{i}": f"v{r}_{i}" for i in range(50)} for r in range(20)]
        raw = json.dumps(rows)
        out = fold_json_to_toon(raw)
        self.assertNotEqual(out, raw, "a 50-column uniform table must columnarize")
        self.assertTrue(_strict_eq(toon_decode(out), json.loads(raw)))

    def test_long_nested_cell_roundtrips(self):
        rows = [
            {"id": i, "owner": {"login": f"u{i}", "node_id": "MDQ6VXNlcj" + str(i) * 40,
                                "avatar_url": "https://avatars.githubusercontent.com/u/583231?v=4"}}
            for i in range(5)
        ]
        raw = json.dumps(rows)
        out = fold_json_to_toon(raw)
        self.assertNotEqual(out, raw, "a long nested object must not reject the table")
        self.assertTrue(_strict_eq(toon_decode(out), json.loads(raw)))

    def test_string_starting_with_equals_roundtrips(self):
        rows = [{"k": "=danger", "n": 1}, {"k": "=also", "n": 2}, {"k": "plain", "n": 3}]
        raw = json.dumps(rows)
        out = fold_json_to_toon(raw)
        self.assertTrue(_strict_eq(toon_decode(out), json.loads(raw)))


class TestJsonReadFileLane(unittest.TestCase):
    def test_single_dict_json_minifies(self):
        data = {"openapi": "3.0.0", "info": {"title": "S", "version": "1.0.0"},
                "paths": {f"/r{i}": {"get": {"summary": f"Get {i}"}} for i in range(40)}}
        raw = json.dumps(data, indent=2)
        out, st = compress_tool_result("read_file", raw, args={"path": "openapi.json"}, session_id="j1")
        self.assertEqual(st.get("gate"), "ok")
        self.assertIn(st.get("kind"), ("file_json_min", "file_toon"))
        self.assertLess(len(out), len(raw))
        body = out.split("\n", 1)[1] if "\n" in out else out
        self.assertEqual(json.loads(body), data, "JSON minify must stay lossless")

    def test_wide_json_array_columnarizes(self):
        rows = [{f"f_{i}": f"v{r}_{i}" for i in range(50)} for r in range(20)]
        raw = json.dumps(rows)
        out, st = compress_tool_result("read_file", raw, args={"path": "wide.json"}, session_id="j2")
        self.assertEqual(st.get("gate"), "ok")
        self.assertEqual(st.get("kind"), "file_toon")
        self.assertLess(len(out), len(raw) * 0.7, "wide uniform JSON should beat 30%")

    def test_small_json_still_passes_through(self):
        raw = json.dumps({"a": 1})
        out, st = compress_tool_result("read_file", raw, args={"path": "tiny.json"}, session_id="j3")
        self.assertEqual(out, raw)


class TestIntentRetrieval(unittest.TestCase):
    def _rank(self, content: str, intent: str, budget: int = 80) -> str:
        ref = ccr_put(content)
        self.assertTrue(ref)
        payload = json.loads(_handle_retrieve({"hash": ref, "intent": intent, "intent_budget": budget}))
        return payload.get("lines", "")

    def test_intent_finds_function_signatures(self):
        body = "\n".join(f"{i}|    x_{i} = {i}" for i in range(1, 400))
        content = "1|def minify_json(text: str) -> tuple[str, int]:\n2|    pass\n" + body
        lines = self._rank(content, "which functions does this file define, signatures")
        self.assertIn("def minify_json", lines)

    def test_intent_finds_heading_versions(self):
        content = "\n".join(
            ["1|# Changelog", "2|", "3|## [4.2.6] - 2026-10-26", "4|", "5|notes"] +
            [f"{i}|filler line {i}" for i in range(6, 300)]
        )
        lines = self._rank(content, "release versions in the changelog")
        self.assertIn("4.2.6", lines)

    def test_intent_is_bounded(self):
        content = "\n".join(f"{i}|error code {i}" for i in range(1, 2000))
        lines = self._rank(content, "error code", budget=20)
        self.assertLessEqual(len(lines.splitlines()), 24)


class TestRewriteCli(unittest.TestCase):
    def test_rewrite_injects_pytest_flags(self):
        buf = io.StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            code = cli_main(["rewrite", "--", "pytest", "tests/"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        self.assertIn("-q", buf.getvalue())
        self.assertIn("--tb=short", buf.getvalue())

    def test_rewrite_respects_explicit_verbose(self):
        buf = io.StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            cli_main(["rewrite", "--", "pytest", "-v"])
        finally:
            sys.stdout = old
        self.assertEqual(buf.getvalue().strip(), "pytest -v")


if __name__ == "__main__":
    unittest.main()
