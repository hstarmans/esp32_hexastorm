import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# fixes pylance warnings about missing functions
if TYPE_CHECKING:

    def include(path: str, **kwargs): ...
    def package(name: str, *args, **kwargs): ...
    def module(path: str, *args, **kwargs): ...
    def require(name: str, **kwargs): ...
    def options(*args, **kwargs): ...


# Determine Base Directory
# If called by make, use the env var. If called manually, use the file location.
if env_manifest := os.environ.get("FROZEN_MANIFEST"):
    base_dir = Path(env_manifest).resolve().parent
else:
    base_dir = Path(__file__).resolve().parent

# Define paths relative to the base_dir
code_dir = base_dir / "control"
frozen_output = code_dir / "frozen_root.py"
root_assets = base_dir / "root"

# Cleanup Old Build Artifacts
if frozen_output.is_file():
    frozen_output.unlink()

## Pack Web Assets (Run inside the base_dir context)
# We use check=True to stop the build immediately if packing fails
try:
    # Clean templates
    subprocess.run(
        ["uv", "run", "pyclean", ".", "--erase", "root/templates/*.py", "--yes"],
        cwd=base_dir,
        check=True,
    )

    # Pack assets
    subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "freezefs",
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
except subprocess.CalledProcessError as e:
    print(f"Error during asset packing: {e}")
    sys.exit(1)

if not frozen_output.is_file():
    raise Exception(f"Failed to generate {frozen_output}")
# We check if 'package' exists in the global scope to know if we are in 'make'
if "package" in globals():
    include("$(PORT_DIR)/boards")
    package("control")
    # https://github.com/miguelgrinberg/microdot
    package("microdot")
    package("utemplate")
    # https://github.com/glenn20/micropython-esp32-ota
    package("ota")
    # now trying https://github.com/micropython/micropython-lib/pull/278
    package("mrequests")
    package("winbond")
    package("tmc")
    # Explicit Hexastorm Modules
    module("hexastorm/__init__.py")
    module("hexastorm/ulabext.py")
    module("hexastorm/config.py")
    module("hexastorm/fpga_host/__init__.py")
    module("hexastorm/fpga_host/interface.py")
    module("hexastorm/fpga_host/syncwrap.py")
    module("hexastorm/fpga_host/micropython.py")
    module("hexastorm/fpga_host/tools.py")
    module("hexastorm/tests/__init__.py")
    module("hexastorm/tests/test_mpy.py")
    # Root Modules
    module("tools.py")
    module("boot.py")
    # Stdlib Requirements
    # Note: 'require' pulls from micropython-lib.
    # 'time' is needed because standard utime often lacks strftime.
    require("time")
    require("pyjwt")
    require("logging")
    require("unittest")
