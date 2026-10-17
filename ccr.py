"""trimtok root shim: forwards to trimtok.ccr."""
from __future__ import annotations
import sys
import trimtok.ccr as _c

sys.modules[__name__] = _c
