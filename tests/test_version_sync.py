import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

class TestVersionUniformity(unittest.TestCase):
    def test_version_single_source(self):
        import trimtok
        from trimtok.core import VERSION
        self.assertEqual(VERSION, trimtok.__version__)
        
        # Verify pyproject.toml matches the single source
        pyproject = pathlib.Path(__file__).parents[1] / "pyproject.toml"
        if pyproject.exists():
            for line in pyproject.read_text(encoding="utf-8").splitlines():
                if line.startswith("version ="):
                    proj_ver = line.split("=")[1].strip().strip('"\'')
                    self.assertEqual(trimtok.__version__, proj_ver)
                    break

        # plugin.yaml version is optional in Hermes; if present, it must match pyproject.
        plugin_yaml = pathlib.Path(__file__).parents[1] / "plugin.yaml"
        if plugin_yaml.exists():
            for line in plugin_yaml.read_text(encoding="utf-8").splitlines():
                if line.startswith("version:"):
                    yaml_ver = line.split(":")[1].strip().strip('"\'')
                    self.assertEqual(trimtok.__version__, yaml_ver)
                    break

        from trimtok import mcp_server
        self.assertEqual(mcp_server.SERVER_INFO["version"], VERSION)

if __name__ == "__main__":
    unittest.main()
