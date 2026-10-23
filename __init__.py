"""trimtok root shim: forwards to trimtok package for backwards compatibility."""
from __future__ import annotations

import importlib.util
import pathlib
import sys

_OUTER_DIR = pathlib.Path(__file__).resolve().parent
_INNER_DIR = _OUTER_DIR / "trimtok"
_INNER_INIT = _INNER_DIR / "__init__.py"

_SEARCH_PATHS = [str(_INNER_DIR), str(_OUTER_DIR)]
__path__ = _SEARCH_PATHS

if "trimtok" not in sys.modules or getattr(sys.modules["trimtok"], "__file__", None) != str(_INNER_INIT):
    _spec = importlib.util.spec_from_file_location(
        "trimtok", _INNER_INIT, submodule_search_locations=_SEARCH_PATHS
    )
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _mod.__path__ = _SEARCH_PATHS
        sys.modules["trimtok"] = _mod
        _spec.loader.exec_module(_mod)
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
