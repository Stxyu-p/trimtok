"""trimtok root shim: forwards to trimtok.toon_codec."""
from __future__ import annotations
import sys
import trimtok.toon_codec as _m

sys.modules[__name__] = _m
