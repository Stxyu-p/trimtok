#!/usr/bin/env python3
"""trimtok root shim: forwards to trimtok.mcp_server."""
from __future__ import annotations
import sys
from trimtok.mcp_server import *

if __name__ == "__main__":
    sys.exit(main())
