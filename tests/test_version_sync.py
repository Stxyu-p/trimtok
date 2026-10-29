import pathlib
import unittest

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

if __name__ == "__main__":
    unittest.main()
