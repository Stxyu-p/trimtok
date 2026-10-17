"""trimtok root shim: forwards to trimtok.dedup."""
from __future__ import annotations
import sys
import trimtok.dedup as _m

sys.modules[__name__] = _m
