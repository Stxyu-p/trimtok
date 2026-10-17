#!/usr/bin/env python3
"""trimtok root shim: forwards to trimtok.__main__."""
from __future__ import annotations
import sys
from trimtok.__main__ import *

if __name__ == "__main__":
    sys.exit(main())
