"""mcp_install edits user config files (Claude Desktop, Codex, Claude Code...).

A bug here corrupts an editor config the user did not ask us to touch, so these
tests pin the two properties that matter: never write something unparseable, and
never drop a key we do not own.
"""
import json
import tempfile
import unittest
from pathlib import Path

from trimtok import mcp_install as mi


def _client(path: Path, kind: str = "json") -> "mi.Client":
    return mi.Client(key="probe", label="Probe", path=lambda: path,
                     detect=lambda: True, kind=kind)


def _spec() -> "mi.ServerSpec":
    return mi.ServerSpec(name="trimtok", module="trimtok.mcp_server", env={"A": "1"})


class JsonConfigTests(unittest.TestCase):
    def test_missing_and_empty_configs_read_as_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            empty = Path(tmp) / "empty.json"
            empty.write_text("   \n", encoding="utf-8")
            self.assertEqual(mi._read_json(missing), {})
            self.assertEqual(mi._read_json(empty), {})

    def test_invalid_json_raises_and_leaves_the_file_untouched(self):
        # The whole point: refuse loudly instead of replacing a hand-edited file.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            original = '{"mcpServers": {"mine": {"command": "x"}},}'
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(mi.ConfigError):
                mi._edit_json(_client(path), _spec(), remove=False, dry_run=False)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_edit_json_preserves_unrelated_keys_and_other_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            path.write_text(json.dumps({
                "theme": "dark",
                "mcpServers": {"theirs": {"command": "keep-me"}},
            }), encoding="utf-8")

            status = mi._edit_json(_client(path), _spec(), remove=False, dry_run=False)

            self.assertEqual(status, "added")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["theme"], "dark")
            self.assertEqual(data["mcpServers"]["theirs"], {"command": "keep-me"})
            self.assertEqual(data["mcpServers"]["trimtok"]["args"], ["-m", "trimtok.mcp_server"])
            self.assertEqual(data["mcpServers"]["trimtok"]["env"], {"A": "1"})

    def test_edit_json_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            client = _client(path)
            self.assertEqual(mi._edit_json(client, _spec(), remove=False, dry_run=False), "added")
            self.assertEqual(mi._edit_json(client, _spec(), remove=False, dry_run=False), "unchanged")

    def test_edit_json_remove_only_drops_its_own_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            client = _client(path)
            mi._edit_json(client, _spec(), remove=False, dry_run=False)
            path_data = json.loads(path.read_text(encoding="utf-8"))
            path_data["mcpServers"]["theirs"] = {"command": "keep-me"}
            path.write_text(json.dumps(path_data), encoding="utf-8")

            self.assertEqual(mi._edit_json(client, _spec(), remove=True, dry_run=False), "removed")

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("trimtok", data["mcpServers"])
            self.assertEqual(data["mcpServers"]["theirs"], {"command": "keep-me"})
            # Removing something already absent is a no-op, not an error.
            self.assertEqual(mi._edit_json(client, _spec(), remove=True, dry_run=False), "absent")

    def test_dry_run_reports_but_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            path.write_text('{"mcpServers": {}}', encoding="utf-8")
            before = path.read_text(encoding="utf-8")

            self.assertEqual(mi._edit_json(_client(path), _spec(), remove=False, dry_run=True), "added")
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_non_object_top_level_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(mi.ConfigError):
                mi._read_json(path)


class AtomicWriteTests(unittest.TestCase):
    def test_creates_parent_and_backs_up_the_original_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "config.json"
            mi._atomic_write(path, '{"a": 1}\n')
            self.assertEqual(path.read_text(encoding="utf-8"), '{"a": 1}\n')

            mi._atomic_write(path, '{"a": 2}\n')
            backup = path.with_name(path.name + ".pre-mcp-install.bak")
            self.assertEqual(path.read_text(encoding="utf-8"), '{"a": 2}\n')
            # The backup keeps the FIRST original; a second write must not clobber it.
            self.assertEqual(backup.read_text(encoding="utf-8"), '{"a": 1}\n')

    def test_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            mi._atomic_write(path, "{}\n")
            leftovers = [p.name for p in Path(tmp).iterdir() if p.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])


class CodexTomlTests(unittest.TestCase):
    ORIGINAL = 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "keep-me"\n'

    def test_strip_removes_the_table_and_its_subtables_only(self):
        text = (
            'model = "gpt-5"\n\n'
            "[mcp_servers.trimtok]\ncommand = \"py\"\nargs = []\n\n"
            "[mcp_servers.trimtok.env]\nA = \"1\"\n\n"
            '[mcp_servers.other]\ncommand = "keep-me"\n'
        )
        stripped, found = mi._strip_toml_table(text, "mcp_servers.trimtok")
        self.assertTrue(found)
        self.assertNotIn("trimtok", stripped)
        self.assertIn('[mcp_servers.other]\ncommand = "keep-me"', stripped)
        self.assertIn('model = "gpt-5"', stripped)

    def test_edit_codex_round_trips_and_preserves_other_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(self.ORIGINAL, encoding="utf-8")
            client = _client(path, kind="codex")

            self.assertEqual(mi._edit_codex(client, _spec(), remove=False, dry_run=False), "added")
            added = path.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.trimtok]", added)
            self.assertIn('command = "keep-me"', added)

            self.assertEqual(mi._edit_codex(client, _spec(), remove=True, dry_run=False), "removed")
            self.assertEqual(path.read_text(encoding="utf-8"), self.ORIGINAL)


if __name__ == "__main__":
    unittest.main()
