"""v5.3.5: content policy, adaptive levels, model-aware TOON, CCR retention,
and a self-check proving the fix-accuracy benchmark can actually fail.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="trimtok-v535-"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from trimtok import _context_pressure, _toon_allowed  # noqa: E402
from trimtok import ccr  # noqa: E402
from trimtok.policy import (  # noqa: E402
    budget_for,
    classify,
    fold_min_lines,
    level_for_pressure,
    looks_secret,
    toon_allowed_for,
)


class _Ctx:
    def __init__(self, **kw):
        self.kw = kw

    def get_config(self, key, default=None):
        return self.kw.get(key, default)


class TestContentPolicy(unittest.TestCase):
    def test_secret_values_stay_verbatim(self):
        for blob in (
            "AKIAIOSFODNN7EXAMPLE",
            "-----BEGIN RSA PRIVATE KEY-----",
            "ghp_" + "a" * 30,
            "password = hunter2hunter2",
        ):
            pol = classify(blob)
            self.assertTrue(pol.verbatim, blob)
            self.assertEqual(pol.reason, "secret_value")

    def test_secret_and_security_paths_stay_verbatim(self):
        for path in (".env", "config/secrets.yaml", ".npmrc", "keys/id_rsa"):
            self.assertEqual(classify("x" * 100, path=path).reason, "secret_path", path)
        for path in ("config/auth/policy.yaml", "security/rules.rego", "src/policies/rbac.json"):
            self.assertEqual(classify("x" * 100, path=path).reason, "security_path", path)

    def test_security_source_code_is_still_compressed(self):
        # AST lane keeps every signature and leaves a ref, so auth code is safe.
        self.assertFalse(classify("x" * 200, path="src/auth/login.py").verbatim)
        self.assertFalse(classify("x" * 200, path="trimtok/policy.py").verbatim)

    def test_patch_payload_stays_verbatim(self):
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(classify(diff, tool="patch").reason, "edit_tool")
        self.assertEqual(classify(diff).reason, "patch_payload")

    def test_plain_output_is_compressible(self):
        pol = classify("INFO request 200 OK\n" * 50)
        self.assertTrue(pol.compress)
        self.assertEqual(pol.level, "normal")

    def test_looks_secret_is_quiet_on_prose(self):
        self.assertEqual(looks_secret("the password policy requires 12 characters"), "")


class TestAdaptiveLevels(unittest.TestCase):
    def test_pressure_maps_to_level(self):
        self.assertEqual(level_for_pressure(0.0), "normal")
        self.assertEqual(level_for_pressure(0.2), "light")
        self.assertEqual(level_for_pressure(0.5), "normal")
        self.assertEqual(level_for_pressure(0.9), "aggressive")

    def test_levels_move_budget_in_the_right_direction(self):
        self.assertGreater(budget_for("light", 120), 120)
        self.assertEqual(budget_for("normal", 120), 120)
        self.assertLess(budget_for("aggressive", 120), 120)

    def test_aggressive_folds_more_than_light(self):
        self.assertLess(fold_min_lines("aggressive"), fold_min_lines("light"))

    def test_hook_reads_pressure_from_kwargs(self):
        ctx = _Ctx()
        self.assertAlmostEqual(_context_pressure(ctx, {"context_pressure": 0.8}), 0.8)
        self.assertAlmostEqual(_context_pressure(ctx, {"context_used_pct": 80}), 0.8)
        self.assertEqual(_context_pressure(ctx, {}), 0.0)

    def test_adaptive_can_be_pinned_off(self):
        ctx = _Ctx(adaptive_enabled=False, context_pressure=0.9)
        self.assertEqual(_context_pressure(ctx, {}), 0.0)


class TestModelAwareToon(unittest.TestCase):
    def test_known_family_gets_toon(self):
        self.assertTrue(toon_allowed_for("claude-sonnet-5", "auto"))
        self.assertTrue(toon_allowed_for("gpt-5.2", "auto"))

    def test_unknown_family_falls_back_to_json(self):
        self.assertFalse(toon_allowed_for("mystery-7b-instruct", "auto"))

    def test_explicit_modes_win(self):
        self.assertTrue(toon_allowed_for("mystery-7b", "always"))
        self.assertFalse(toon_allowed_for("claude-sonnet-5", "never"))

    def test_hook_defaults_keep_toon_when_family_unknown(self):
        self.assertTrue(_toon_allowed(_Ctx()))
        self.assertFalse(_toon_allowed(_Ctx(toon_enabled=False)))
        self.assertFalse(_toon_allowed(_Ctx(toon_mode="never")))
        self.assertFalse(_toon_allowed(_Ctx(model_family="mystery-7b")))

    def test_json_lane_honours_toon_enabled(self):
        from trimtok.dedup import compress_tool_result
        from trimtok.signals import Deadline

        rows = [{f"f{i}": f"v{r}_{i}" for i in range(30)} for r in range(20)]
        raw = json.dumps(rows)
        out_on, st_on = compress_tool_result(
            "read_file", raw, args={"path": "d.json"}, session_id="m1",
            toon_enabled=True, deadline=Deadline(500))
        out_off, st_off = compress_tool_result(
            "read_file", raw, args={"path": "d.json"}, session_id="m2",
            toon_enabled=False, deadline=Deadline(500))
        self.assertEqual(st_on.get("kind"), "file_toon")
        self.assertEqual(st_off.get("kind"), "file_json_min")
        # The JSON fallback must still be lossless.
        body = "\n".join(out_off.splitlines()[1:])
        self.assertEqual(json.loads(body), rows)
        self.assertLess(len(out_on), len(out_off), "TOON should beat minified JSON")


class TestPersistentRecovery(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="trimtok-ccr-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_evict_to_size_drops_oldest_first(self):
        store = ccr._get_store(self.dir)
        refs = [store.put(os.urandom(4000).hex()) for _ in range(5)]
        before = store.stats()["entries"]
        self.assertEqual(before, 5)
        store.evict_to_size(store.stats()["bytes"] // 2)
        after = store.stats()["entries"]
        self.assertLess(after, before)
        # Newest survives, oldest is gone.
        self.assertIsNotNone(store.get(refs[-1]))
        self.assertIsNone(store.get(refs[0]))

    def test_ttl_roundtrips_through_meta(self):
        store = ccr._get_store(self.dir)
        self.assertEqual(store.ttl_hours(24), 24)
        store.set_ttl_hours(72)
        self.assertEqual(store.ttl_hours(24), 72)

    def test_evict_is_a_noop_under_the_cap(self):
        store = ccr._get_store(self.dir)
        store.put("small")
        self.assertEqual(store.evict_to_size(50 * 1024 * 1024), 0)


class TestFixAccuracyBenchmarkCanFail(unittest.TestCase):
    """A benchmark that cannot fail measures nothing."""

    def _run(self, task_id: str, replacement: str) -> tuple[bool, str]:
        import fix_accuracy as fa

        task = next(t for t in fa.TASKS if t["id"] == task_id)
        src = (ROOT / task["file"]).read_text(encoding="utf-8")
        buggy = fa.apply_mutation(task, src)
        lineno = fa.line_of(buggy, task["buggy"]) or fa.line_of(buggy, task["original"])
        lines = buggy.splitlines()
        lines[lineno - 1] = replacement
        patched = "\n".join(lines) + "\n"

        tmp = Path(tempfile.mkdtemp(prefix="trimtok-probe-"))
        try:
            shutil.copytree(ROOT / "trimtok", tmp / "trimtok",
                            ignore=shutil.ignore_patterns("__pycache__"))
            (tmp / task["file"]).write_text(patched, encoding="utf-8")
            return fa._exec_probe(tmp, task["probe"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_correct_patch_passes(self):
        ok, err = self._run("policy-budget-clamp", "    return max(10, int(default * mult))")
        self.assertTrue(ok, err)

    def test_wrong_patch_fails(self):
        ok, _ = self._run("policy-budget-clamp", "    return min(10, int(default * mult))")
        self.assertFalse(ok, "the probe must reject the still-buggy line")

    def test_extract_line_reads_numbered_reply(self):
        import fix_accuracy as fa

        # The reply's own spacing is discarded; the buggy line's indent wins.
        self.assertEqual(fa.extract_line("42|return 1", 42, indent="    "), "    return 1")
        self.assertEqual(fa.extract_line("42|      return 1", 42, indent="    "), "    return 1")
        self.assertEqual(fa.extract_line("```\n7| x = 1\n```", 99, indent=""), " x = 1")


if __name__ == "__main__":
    unittest.main()
