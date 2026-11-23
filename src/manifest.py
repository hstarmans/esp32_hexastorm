import logging
import os
import subprocess
import sys
import time
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

try:
    from typing import TYPE_CHECKING
except ImportError:
    TYPE_CHECKING = False

# Configure logging to standard output
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class AssetGenerationError(Exception):
    """Raised when frozen root asset generation fails."""


# fixes pylance warnings about missing functions
if TYPE_CHECKING:

    def include(path: str, **kwargs): ...
    def package(name: str, *args, **kwargs): ...
    def module(path: str, *args, **kwargs): ...
    def require(name: str, **kwargs): ...
    def options(*args, **kwargs): ...


def _load_paths_helper(control_dir: Path):
    spec = spec_from_file_location("_control_paths", control_dir / "_paths.py")
    if spec is None or spec.loader is None:
        raise AssetGenerationError("Failed to load control/_paths.py helper")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Determine Base Directory
# If called by make, use the env var. If called manually, use the file location.
if env_manifest := os.environ.get("FROZEN_MANIFEST"):
    base_dir = Path(env_manifest).resolve().parent
else:
    base_dir = Path(__file__).resolve().parent

repo_root = base_dir.parent

# Define paths relative to the base_dir
code_dir = base_dir / "control"
frozen_output = code_dir / "frozen_root.py"
root_assets = base_dir / "root"
paths_helper = _load_paths_helper(code_dir)

# Cleanup Old Build Artifacts
if frozen_output.is_file():
    frozen_output.unlink()

## Pack Web Assets (Run inside the base_dir context)
# We use check=True to stop the build immediately if packing fails
try:
    # jobs folder can be created by test server, clean it up first
    job_folder = root_assets / "sd" / "jobs"

    # Check if folder exists and has files
    if job_folder.exists():
        logger.info(f"Cleaning local test files from: {job_folder}")
        for item in job_folder.iterdir():
            if item.is_file():
                # Delete the file
                item.unlink()

    # Clean templates
    subprocess.run(
        [
            *paths_helper.python_module_cmd("pyclean"),
            ".",
            "--erase",
            "root/templates/*.py",
            "--yes",
        ],
        cwd=base_dir,
        check=True,
    )

    fpga_bit = root_assets / "sd" / "fpga" / "fpga.bit"
    if os.environ.get("ALLOW_MISSING_FPGA") != "1" and (
        not fpga_bit.is_file() or fpga_bit.stat().st_size == 0
    ):
        raise AssetGenerationError(
            f"Missing FPGA bitstream at {fpga_bit}. "
            "Build with Docker ('uv run build --docker') or pass '--fpga-bit <path>'."
        )

    # Pack assets
    subprocess.run(
        [
            *paths_helper.python_module_cmd("freezefs"),
            str(root_assets),  # Source
            str(frozen_output),  # Destination
            "--target=/",
            "--on-import=extract",
            "--overwrite=always",
            "--compress",
        ],
        cwd=base_dir,
        check=True,
    )

    build_id = f"build_{int(time.time())}"
    with open(frozen_output, "a") as f:
        f.write(f'\nBUILD_ID = "{build_id}"\n')

    hexastorm_src_path = paths_helper.resolve_hexastorm_src(repo_root)
    firmware_version = paths_helper.resolve_firmware_version(repo_root)
    fpga_version = paths_helper.resolve_fpga_version(
        repo_root,
        hexastorm_src=hexastorm_src_path,
        bit_path=fpga_bit if fpga_bit.is_file() else None,
    )
    build_info_output = code_dir / "build_info.py"
    with open(build_info_output, "w") as f:
        f.write(f'BUILD_ID = "{build_id}"\n')
        f.write(f'FIRMWARE_VERSION = "{firmware_version}"\n')
        f.write(f'FPGA_VERSION = "{fpga_version}"\n')
except subprocess.CalledProcessError as e:
    logger.error(f"Error during asset packing: {e}")
    sys.exit(1)

if not frozen_output.is_file():
    raise AssetGenerationError(f"Failed to generate {frozen_output}")

# We check if 'package' exists in the global scope to know if we are in 'make'
if "package" in globals():
    vendor_dir = str(repo_root / "vendor")
    hexastorm_src_dir = str(hexastorm_src_path)
    camera_api_src = repo_root / "deps" / "micropython-camera-API" / "src"

    if not (camera_api_src / "acamera.py").is_file():
        raise AssetGenerationError(
            f"Missing camera driver module at {camera_api_src / 'acamera.py'}. "
            "Run 'git submodule update --init --recursive'."
        )

    include("$(PORT_DIR)/boards")
    package("control")

    # Pure-Python vendored MicroPython dependencies (see vendor/VERSIONS.md)
    package("microdot", base_path=vendor_dir)
    package("utemplate", base_path=vendor_dir)
    package("ota", base_path=vendor_dir)
    package("mrequests", base_path=vendor_dir)
    package("winbond", base_path=vendor_dir)
    package("tmc", base_path=vendor_dir)

    # Explicit Hexastorm Modules
    module("hexastorm/__init__.py", base_path=hexastorm_src_dir)
    module("hexastorm/ulabext.py", base_path=hexastorm_src_dir)
    module("hexastorm/config.py", base_path=hexastorm_src_dir)
    module("hexastorm/calibration/__init__.py", base_path=hexastorm_src_dir)
    module("hexastorm/calibration/core.py", base_path=hexastorm_src_dir)
    module("hexastorm/calibration/vector_lite.py", base_path=hexastorm_src_dir)
    module("hexastorm/fpga_host/__init__.py", base_path=hexastorm_src_dir)
    module("hexastorm/fpga_host/interface.py", base_path=hexastorm_src_dir)
    module("hexastorm/fpga_host/syncwrap.py", base_path=hexastorm_src_dir)
    module("hexastorm/fpga_host/micropython.py", base_path=hexastorm_src_dir)
    module("hexastorm/fpga_host/tools.py", base_path=hexastorm_src_dir)
    module("hexastorm/tests/__init__.py", base_path=hexastorm_src_dir)
    module("hexastorm/tests/test_mpy.py", base_path=hexastorm_src_dir)

    # Root Modules
    module("tools.py")
    module("boot.py")
    module("main.py")

    # Stdlib Requirements from micropython-lib
    require("time")
    require("pyjwt")
    require("logging")
    require("unittest")

    # Camera Python wrapper from submodule
    module("acamera.py", base_path=str(camera_api_src))
