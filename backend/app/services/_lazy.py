"""Lazy module import helper to keep heavy SDKs off the startup critical path."""

import importlib
from typing import Any


class LazyModule:
    """A proxy that imports the underlying module on first attribute access.

    Some SDKs (e.g. ``lark_oapi``) eagerly load a large API tree on import, adding
    tens of seconds to process startup. Wrapping them in this proxy defers that
    cost until the module is actually used at runtime, so it stays off the
    application's startup critical path (which matters for deploy health checks
    that expect the server port to open quickly).
    """

    def __init__(self, module_name: str) -> None:
        self._module_name = module_name
        self._module: Any = None

    def __getattr__(self, name: str) -> Any:
        # __getattr__ only runs when normal attribute lookup fails. ``_module``
        # and ``_module_name`` are set in __init__, so they resolve normally and
        # never recurse through here.
        mod = self._module
        if mod is None:
            mod = importlib.import_module(self._module_name)
            self._module = mod
        return getattr(mod, name)
