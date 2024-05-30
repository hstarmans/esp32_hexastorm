import os
import subprocess
from pathlib import Path


# FROZEN manifest environmental variable is set
# while calling make from the micropython dir
# make FROZEN_MANIFEST = "PATHHERE/manifest.py"
base_dir = os.environ.get("FROZEN_MANIFEST")

# another option is to call it
# using python -m src.manifest, i.e. without make
# in this case there is no "FROZEN_MANIFEST"
if not base_dir:
    base_dir = Path(__file__).resolve().parent
else:
    base_dir = Path(base_dir).resolve().parent

code = Path("control")
root = Path("frozen_root.py")

if (base_dir / code / root).is_file():
    os.remove(base_dir / code / root)

work_dir = os.getcwd()
os.chdir(base_dir)

# remove build left overs from templates
subprocess.run(
    [
        "poetry",
        "run",
        "pyclean",
        ".",
        "--erase",
        "root/templates/*.py",
        "--yes",
    ]
)
# pack templates
subprocess.run(
    [
        "poetry",
        "run",
        "python",
        "-m",
        "freezefs",
        "root/",
        "control/frozen_root.py",
        "--target=/",
        "--on-import=extract",
        "--compress",
    ]
)

os.chdir(work_dir)

if not (base_dir / code / root).is_file():
    raise Exception("Frozen files are not created")


called_by_make = True
try:
    package
# script is called via python -m src.manifest
except NameError:
    called_by_make = False
# script called via make
except EOFError:
    pass

if called_by_make:
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
    module("hexastorm/__init__.py")
    module("hexastorm/controller.py")
    module("hexastorm/ulabext.py")
    module("hexastorm/constants.py")
    module("hexastorm/tests/__init__.py")
    module("hexastorm/tests/test_electrical.py")
    require("pyjwt")
    require("logging")
    require("unittest")
    # module("boot.py")
