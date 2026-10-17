"""trimtok root shim: forwards to trimtok.delta_engine."""
from __future__ import annotations
import sys
import trimtok.delta_engine as _m

sys.modules[__name__] = _m
