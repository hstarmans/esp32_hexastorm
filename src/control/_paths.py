"""Path resolution helpers for CPython tooling, tests, and firmware manifest builds."""

import os
import sys
from importlib.util import find_spec
from pathlib import Path


def get_repo_root() -> Path:
    """Return the root directory of the esp32_hexastorm repository."""
    return Path(__file__).resolve().parents[2]


def python_module_cmd(module_name: str) -> list[str]:
    """Return command prefix to run a Python module tool in the current env or via uv."""
    if find_spec(module_name) is not None:
        return [sys.executable, "-m", module_name]
    return ["uv", "run", "python", "-m", module_name]


def resolve_hexastorm_src(
    repo_root: Path | None = None,
    require_calibration_core: bool = True,
) -> Path:
    """Locate the ``hexastorm`` package source directory (directory containing ``hexastorm/``).

    Resolution order:
    1. ``HEXASTORM_DIR`` environment variable (repo root, ``src/`` dir, or package dir).
    2. Tracked submodule ``deps/hexastorm/src``.
    3. Sibling development checkout ``../hexastorm/src``.

    When ``require_calibration_core`` is True, a checkout containing
    ``hexastorm/calibration/core.py`` is preferred before falling back to any valid
    ``hexastorm/__init__.py`` directory.
    """
    env_dir = os.environ.get("HEXASTORM_DIR")
    if env_dir:
        candidate = Path(env_dir).expanduser().resolve()
        for base in (candidate, candidate / "src", candidate.parent):
            if (base / "hexastorm" / "__init__.py").is_file() and (
                base == candidate
                or base == candidate / "src"
                or candidate.name == "hexastorm"
            ):
                return base
        raise FileNotFoundError(
            f"HEXASTORM_DIR={env_dir!r} does not contain a 'hexastorm' package."
        )

    root = get_repo_root() if repo_root is None else Path(repo_root).resolve()
    candidates = [
        root / "deps" / "hexastorm" / "src",
        root.parent / "hexastorm" / "src",
    ]

    if require_calibration_core:
        for base in candidates:
            if (base / "hexastorm" / "__init__.py").is_file() and (
                base / "hexastorm" / "calibration" / "core.py"
            ).is_file():
                return base

    for base in candidates:
        if (base / "hexastorm" / "__init__.py").is_file():
            return base

    raise FileNotFoundError(
        "Could not locate 'hexastorm' source directory. "
        "Run 'git submodule update --init --recursive' or set HEXASTORM_DIR."
    )


def configure_cpython_paths(repo_root: Path | None = None) -> dict[str, Path]:
    """Ensure ``src``, ``vendor``, and ``hexastorm`` src directories are on ``sys.path``."""
    root = get_repo_root() if repo_root is None else Path(repo_root).resolve()
    resolved: dict[str, Path] = {
        "src": root / "src",
        "vendor": root / "vendor",
    }

    try:
        resolved["hexastorm_src"] = resolve_hexastorm_src(root)
    except FileNotFoundError:
        if os.environ.get("HEXASTORM_DIR"):
            raise

    existing = set(sys.path)
    for key in ("hexastorm_src", "vendor", "src"):
        path = resolved.get(key)
        if path is not None:
            path_str = str(path)
            if path_str not in existing:
                sys.path.insert(0, path_str)
                existing.add(path_str)

    return resolved
