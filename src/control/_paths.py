"""Path resolution helpers for CPython tooling, tests, and firmware manifest builds."""

import hashlib
import json
import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

FPGA_HDL_FILES = (
    "build.py",
    "config.py",
    "core.py",
    "lasers.py",
    "movement.py",
    "platforms.py",
    "pwm.py",
    "resources.py",
    "spi_helpers.py",
    "utils.py",
)
FPGA_HDL_DIRS = ("blocks", "luna")


def get_repo_root() -> Path:
    """Return the root directory of the esp32_hexastorm repository."""
    return Path(__file__).resolve().parents[2]


def python_module_cmd(module_name: str) -> list[str]:
    """Return command prefix to run a Python module tool in the current env or via uv."""
    if find_spec(module_name) is not None:
        return [sys.executable, "-m", module_name]
    return ["uv", "run", "python", "-m", module_name]


def resolve_firmware_version(repo_root: Path | None = None) -> str:
    """Determine the firmware version string to freeze into build_info.py."""
    env_ver = os.environ.get("FIRMWARE_VERSION", "").strip()
    if env_ver:
        return env_ver

    root = get_repo_root() if repo_root is None else Path(repo_root).resolve()
    for git_args in (
        ["git", "-C", str(root), "describe", "--tags", "--exact-match"],
        ["git", "-C", str(root), "describe", "--tags", "--abbrev=0"],
    ):
        try:
            proc = subprocess.run(
                git_args,
                capture_output=True,
                text=True,
                check=False,
            )
            tag = proc.stdout.strip()
            if proc.returncode == 0 and tag:
                return tag
        except OSError:
            break

    config_path = root / "src" / "root" / "config.json"
    try:
        data = json.loads(config_path.read_text())
        ver = data.get("github", {}).get("version")
        if ver:
            return str(ver)
    except (OSError, ValueError):
        pass

    return "0.1"


def compute_fpga_source_hash(hexastorm_src: Path) -> str:
    """Compute a deterministic SHA-256 digest of the iCE40UP5K HDL synthesis sources."""
    pkg_dir = Path(hexastorm_src).resolve() / "hexastorm"
    if not (pkg_dir / "core.py").is_file():
        raise FileNotFoundError(f"Missing hexastorm HDL sources at {pkg_dir}")

    files: list[Path] = []
    for rel_file in FPGA_HDL_FILES:
        candidate = pkg_dir / rel_file
        if candidate.is_file():
            files.append(candidate)

    for rel_dir in FPGA_HDL_DIRS:
        sub_dir = pkg_dir / rel_dir
        if sub_dir.is_dir():
            files.extend(p for p in sub_dir.rglob("*.py") if p.is_file())

    hasher = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(pkg_dir).as_posix()):
        rel = path.relative_to(pkg_dir).as_posix().encode("utf-8")
        hasher.update(rel + b"\0")
        hasher.update(path.read_bytes() + b"\0")
    return hasher.hexdigest()


def resolve_fpga_version(
    repo_root: Path | None = None,
    hexastorm_src: Path | None = None,
    bit_path: Path | None = None,
) -> str:
    """Determine the FPGA gateware version string to freeze into build_info.py."""
    env_ver = os.environ.get("FPGA_VERSION", "").strip()
    if env_ver:
        return env_ver

    root = get_repo_root() if repo_root is None else Path(repo_root).resolve()

    if hexastorm_src is not None:
        hs_path = Path(hexastorm_src).resolve()
        hs_repo = hs_path.parent if hs_path.name == "src" else hs_path
    else:
        try:
            resolved = resolve_hexastorm_src(root, require_calibration_core=False)
            hs_repo = resolved.parent if resolved.name == "src" else resolved
        except FileNotFoundError:
            hs_repo = root / "deps" / "hexastorm"

    commit = ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(hs_repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
    except OSError:
        pass

    bit_short = ""
    if bit_path is not None:
        bit_candidate = Path(bit_path)
        if bit_candidate.is_file():
            bit_short = hashlib.sha256(bit_candidate.read_bytes()).hexdigest()[:8]

    if commit and bit_short:
        return f"{commit}-{bit_short}"
    if bit_short:
        return f"sha256-{bit_short}"
    if commit:
        return commit

    config_path = root / "src" / "root" / "config.json"
    try:
        data = json.loads(config_path.read_text())
        ver = data.get("fpga", {}).get("version")
        if ver:
            return str(ver)
    except (OSError, ValueError):
        pass

    return "0.1"


def resolve_hexastorm_src(
    repo_root: Path | str | None = None,
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
