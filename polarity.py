"""trimtok root shim: forwards to trimtok.signals."""
from __future__ import annotations
import sys
import trimtok.signals as _m

sys.modules[__name__] = _m
