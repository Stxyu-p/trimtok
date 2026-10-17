"""trimtok root shim: forwards to trimtok.pruners."""
from __future__ import annotations
import sys
import trimtok.pruners as _m

sys.modules[__name__] = _m
