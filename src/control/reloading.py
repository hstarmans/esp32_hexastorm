"""
Hot reloading utilities for MicroPython.

This module provides two helpers:

    reload(module_or_name)
        Recursively reload a module (and its already-imported submodules).
        Accepts either a module object or its qualified name as a string.
        Returns the reloaded root module object.

    hot_reload(*names, g=None)
        Reload one or more modules and automatically assign them into
        the given globals() (default: caller's globals).
        This lets you refresh your REPL or script environment without
        manual re-assignment.

Usage in the REPL (Thonny, mpremote, etc.):

    import myreload   # whatever you named this file

    # reload a single module
    import a
    a = myreload.reload(a)
    a.hond()

    # reload dependencies first
    b = myreload.reload("b")
    a = myreload.reload("a")
    a.hond()

    # shorter: update globals in one go
    myreload.hot_reload("b", "a", g=globals())
    a.hond()

Notes & limitations:
- Always reload dependencies before their parents (e.g. reload "b" before "a" if a.py imports b).
- Avoid `from a import hond` in your code; prefer `import a` and call `a.hond()`.
- Existing instances and old references keep pointing to old code objects; recreate them if needed.
- Frozen/native modules cannot be reloaded.
"""

import sys
import logging

logger = logging.getLogger(__name__)


def reload(module_or_name):
    """
    Reload a package tree (root + its already-imported submodules) on MicroPython.
    Accepts a module object or its qualified name (str).
    Returns the reloaded root module.
    """
    root_name = (
        module_or_name if isinstance(module_or_name, str) else module_or_name.__name__
    )

    # Ensure root is importable at least once
    if root_name not in sys.modules:
        __import__(root_name)

    # Snapshot names to touch
    to_reload = [
        n for n in list(sys.modules) if n == root_name or n.startswith(root_name + ".")
    ]

    if not to_reload:
        __import__(root_name)
        return sys.modules[root_name]

    # Delete deepest-first
    for name in sorted(to_reload, key=len, reverse=True):
        sys.modules.pop(name, None)

    # Re-import parents before children, without kwargs; walk the dotted chain
    for name in sorted(to_reload, key=len):
        try:
            parts = name.split(".")
            for i in range(len(parts)):
                __import__(".".join(parts[: i + 1]))
        except Exception as e:
            logger.error("Error re-importing {}: {}".format(name, e))

    return sys.modules.get(root_name)


def hot_reload(*names, g=None):
    """Reload modules and assign them into the given globals() (default: caller's)."""
    if g is None:
        g = globals()
    for n in names:
        reload(n)
        # bind under its top-level name (e.g. "pkg.sub" -> "pkg")
        top = n.split(".")[0]
        g[top] = sys.modules[top]
