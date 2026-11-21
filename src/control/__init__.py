import sys

if sys.implementation.name != "micropython":
    from ._paths import configure_cpython_paths

    configure_cpython_paths()
