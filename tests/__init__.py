import os
import tempfile

# Global test-suite isolation: direct unittest discovery never writes to production ~/.trimtok
os.environ.setdefault("TRIMTOK_DATA_DIR", tempfile.mkdtemp(prefix="trimtok-test-suite-"))
