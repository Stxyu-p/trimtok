"""trimtok root shim: forwards to trimtok.ccr with v2 FastCCRStore alias contract."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any
import trimtok.ccr as _c

FastCCRStore = _c.FastCCRStore
default_dir = _c.default_dir
stats = _c.stats
purge = _c.purge
close = _c.close


def put(content: str, *, dir: Path | None = None, ttl_hours: int = 24, **kwargs: Any) -> str:
    """Store content in FastCCRStore, always returning Base62 alias (#c1)."""
    target = Path(dir) if dir else _c.default_dir()
    store = _c._get_store(target)
    return store.put(content, ttl_hours=ttl_hours)


def get(ref: str, *, dir: Path | None = None, ttl_hours: int = 24, **kwargs: Any) -> str | None:
    """Retrieve content from FastCCRStore by alias (#c1) or sha12."""
    target = Path(dir) if dir else _c.default_dir()
    store = _c._get_store(target)
    return store.get(ref)


# Re-export remaining attributes
for _k, _v in _c.__dict__.items():
    if not _k.startswith("__") and _k not in globals():
        globals()[_k] = _v
