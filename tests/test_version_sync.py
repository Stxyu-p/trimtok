import unittest

class TestVersionUniformity(unittest.TestCase):
    def test_version_single_source(self):
        import trimtok
        from trimtok.core import VERSION
        self.assertEqual(trimtok.__version__, "5.0.0")
        self.assertEqual(VERSION, trimtok.__version__)

if __name__ == "__main__":
    unittest.main()
