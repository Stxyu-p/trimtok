"""trimtok root shim: forwards to trimtok package for backwards compatibility."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

_OUTER_DIR = pathlib.Path(__file__).resolve().parent
_INNER_DIR = _OUTER_DIR / "trimtok"
_INNER_INIT = _INNER_DIR / "__init__.py"

_SEARCH_PATHS = [str(_INNER_DIR), str(_OUTER_DIR)]
__path__ = _SEARCH_PATHS


def _norm_path(p: object) -> str:
    if not isinstance(p, str) or not p:
        return ""
    try:
        return os.path.normcase(os.path.realpath(p))
    except OSError:
        return os.path.normcase(p)


_existing_mod = sys.modules.get("trimtok")
if _existing_mod is None or _norm_path(getattr(_existing_mod, "__file__", None)) != _norm_path(str(_INNER_INIT)):
    _spec = importlib.util.spec_from_file_location(
        "trimtok", _INNER_INIT, submodule_search_locations=_SEARCH_PATHS
    )
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _mod.__path__ = _SEARCH_PATHS
        sys.modules["trimtok"] = _mod
        try:
            _spec.loader.exec_module(_mod)
        except Exception:
            sys.modules.pop("trimtok", None)
            raise
    else:
        import trimtok as _mod
else:
    _mod = sys.modules["trimtok"]

sys.modules[__name__] = _mod

# A host that loads this file under a namespaced module name keeps a reference
# to THIS module object and looks for register() on it (the Hermes plugin
# loader does getattr(module, "register")); swapping sys.modules above does not
# update that reference, so re-export the canonical entry point here.
register = _mod.register
TrimtokContextEngine = getattr(_mod, "TrimtokContextEngine", None)
ContextEngine = getattr(_mod, "ContextEngine", None)

