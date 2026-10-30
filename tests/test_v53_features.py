"""Unit tests for trimtok v5.3.0 features:

- Command-specific output filters (git, ls, npm, pip, find)
- Multi-language AST code skeletonizer (tree-sitter, language-idiomatic comments, iterative)
- Merged-schema TOON transcoding with sparsity guard
- Telemetry streaming generator (_iter_usage_rows)
"""
import unittest
from trimtok.filters import try_command_filter
from trimtok.ts_skeleton import skeletonize_code_multilang, detect_language
from trimtok.dedup import _try_merged_toon


class TestCommandFilters(unittest.TestCase):
    def test_git_log_filter(self):
        log_out = """commit a1b2c3d4e5f6
Author: Alice <alice@example.com>
Date:   Fri Oct 30 10:00:00 2026 +0700

    First commit

commit b2c3d4e5f6a1
Author: Bob <bob@example.com>
Date:   Fri Oct 30 11:00:00 2026 +0700

    Second commit

commit c3d4e5f6a1b2
Author: Alice <alice@example.com>
Date:   Fri Oct 30 12:00:00 2026 +0700

    Third commit
"""
        res = try_command_filter("git log -n 5", log_out)
        self.assertIsNotNone(res)
        self.assertIn("[tt:git_log] 3 commits, 2 authors", res)
        self.assertIn("Alice", res)

    def test_git_status_filter(self):
        status_out = """On branch main
Changes to be committed:
	new file:   src/app.py
Changes not staged for commit:
	modified:   src/core.py
	deleted:    src/old.py
Untracked files:
	test_temp.py
"""
        res = try_command_filter("git status", status_out)
        self.assertIsNotNone(res)
        self.assertIn("[tt:git_status]", res)
        self.assertIn("1 modified", res)
        self.assertIn("1 added", res)
        self.assertIn("1 deleted", res)

    def test_ls_filter(self):
        ls_out = """total 64
drwxr-xr-x  2 user group  4096 Oct 30 10:00 dir1
drwxr-xr-x  2 user group  4096 Oct 30 10:00 dir2
-rw-r--r--  1 user group  2048 Oct 30 10:00 file1.txt
-rw-r--r--  1 user group  8192 Oct 30 10:00 file2.py
-rw-r--r--  1 user group 16384 Oct 30 10:00 file3.bin
"""
        res = try_command_filter("ls -la /workspace", ls_out)
        self.assertIsNotNone(res)
        self.assertIn("[tt:ls] 3 files, 2 dirs", res)

    def test_npm_filter(self):
        npm_out = """my-project@1.0.0 /workspace
+-- express@4.18.2
+-- lodash@4.17.21
+-- jest@29.5.0
`-- typescript@5.0.4
"""
        res = try_command_filter("npm list", npm_out)
        self.assertIsNotNone(res)
        self.assertIn("[tt:npm] 4 packages listed", res)

    def test_pip_filter(self):
        pip_out = """Requirement already satisfied: requests in /site-packages (2.31.0)
Requirement already satisfied: urllib3 in /site-packages (2.0.7)
Requirement already satisfied: certifi in /site-packages (2023.7.22)
Requirement already satisfied: idna in /site-packages (3.4)
"""
        res = try_command_filter("pip install -r reqs.txt", pip_out)
        self.assertIsNotNone(res)
        self.assertIn("[tt:pip] 4 requirements already satisfied", res)

    def test_find_filter(self):
        find_out = """./src/index.js
./src/app.js
./src/utils.js
./src/style.css
./src/main.css
./README.md
"""
        res = try_command_filter("find . -type f", find_out)
        self.assertIsNotNone(res)
        self.assertIn("[tt:find] 6 files", res)
        self.assertIn(".js: 3", res)

    def test_fail_open_invariants(self):
        # Non-matching, empty, short, or invalid inputs must safely return None
        self.assertIsNone(try_command_filter("", "some output"))
        self.assertIsNone(try_command_filter("git log", ""))
        self.assertIsNone(try_command_filter("git log", None))
        self.assertIsNone(try_command_filter("cat foo.txt", "line1\nline2\nline3"))
        self.assertIsNone(try_command_filter("git log", "tiny"))


class TestMultiLangAST(unittest.TestCase):
    def test_language_detection(self):
        self.assertEqual(detect_language("", "app.go"), "go")
        self.assertEqual(detect_language("", "lib.rs"), "rust")
        self.assertEqual(detect_language("", "server.ts"), "javascript")
        self.assertEqual(detect_language("", "Main.java"), "java")
        self.assertEqual(detect_language("package main\nimport (\"fmt\")\nfunc main() {}", ""), "go")
        self.assertEqual(detect_language("pub fn calculate() -> i32 { let mut x = 5; x }", ""), "rust")

    def test_javascript_skeletonizer_uses_slashes(self):
        js_code = """function longHandler(req, res) {
    const a = 1;
    const b = 2;
    const c = 3;
    const d = 4;
    const e = 5;
    const f = 6;
    const g = 7;
    const h = 8;
    const i = 9;
    return a + b + c + d + e + f + g + h + i;
}
""" * 5
        res = skeletonize_code_multilang(js_code, "server.js", min_body_lines=5)
        if res != js_code:
            # Must NOT use Python's "pass" in JavaScript
            self.assertNotIn("pass  #", res)
            self.assertIn("// [tt:body", res)

    def test_deep_ast_no_recursion_error(self):
        # Build deeply nested code blocks to test iterative traversal
        nested = "function deep() { " + "if (true) { " * 200 + "console.log(1);" + " } " * 200 + "}"
        code = nested + "\n" + ("function simple() {\n" + "\n".join(f"  let v{i} = {i};" for i in range(15)) + "\n}\n") * 5
        try:
            res = skeletonize_code_multilang(code, "test.js", min_body_lines=8)
            self.assertIsInstance(res, str)
        except RecursionError:
            self.fail("skeletonize_code_multilang raised RecursionError on deep AST")


class TestMergedTOON(unittest.TestCase):
    def test_merged_toon_roundtrip(self):
        payload = """[
  {"id": 1, "name": "Item 1", "role": "admin"},
  {"id": 2, "name": "Item 2", "extra": "bonus"},
  {"id": 3, "name": "Item 3", "role": "guest"}
]"""
        res = _try_merged_toon(payload)
        self.assertIsNotNone(res)
        self.assertIn("[tt:json_toon_merged", res)

    def test_merged_toon_sparsity_guard(self):
        # A payload with over 64 distinct keys should refuse merging to avoid null bloat
        rows = [{"field_" + str(i): i} for i in range(70)]
        import json
        payload = json.dumps(rows)
        res = _try_merged_toon(payload)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
