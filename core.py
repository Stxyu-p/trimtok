"""trimtok root shim: forwards to trimtok.core."""
from __future__ import annotations
import sys
import trimtok.core as _m

sys.modules[__name__] = _m
