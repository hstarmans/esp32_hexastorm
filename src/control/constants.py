import json
import logging
import sys
import os
import machine


ESP32 = False if sys.platform in ["linux", "win32", "darwin"] else True
logger = logging.getLogger(__name__)


def hardware_init():
    """Fast hardware fixes run immediately on boot"""
    # UART RX and TX connected via resistor
    # and TMC2209, which results in endless
    # communication and failure of micropython shell
    # fix is to set UART1 to zero
    machine.Pin(43, machine.Pin.OUT).value(0)
    machine.Pin(44, machine.Pin.OUT).value(0)


def deploy_assets(overwrite=False):
    """Extracts frozen assets only if a sentinel file is missing."""

    # Check for sentinel file (fastest check)
    if not overwrite:
        try:
            os.stat("/templates/home.html")
            logging.info("Assets already deployed. Skipping extraction.")
            return
        except OSError:
            pass  # File missing, proceed to extract

    logging.info("First boot detected. Initializing asset extraction...")

    # Once you import frozen_root, the on-import hooks will run
    # files get extracted and overwrite
    try:
        from . import frozen_root
    except ImportError:
        logging.error("Could not import frozen_root. Is the build correct?")
        return

    # Perform the extraction
    logging.info("Extracting static files to filesystem...")


def recurse_dct(dct, target, replace):
    for key, val in dct.items():
        if isinstance(val, dict):
            recurse_dct(val, target, replace)
        elif isinstance(val, str):
            dct[key] = val.replace(target, replace)


def load_config():
    """Load json config settings."""
    fname = "config.json" if ESP32 else "src/root/config.json"
    try:
        with open(fname) as f:
            dct = json.load(f)
            if not ESP32:
                recurse_dct(dct, "sd/", "src/root/sd/")
    except OSError:
        logger.warning("Could not load config.json, using defaults")
        dct = {}
    return dct


def update_config():
    """Update the json settings."""
    fname = "config.json" if ESP32 else "src/root/config.json"
    with open(fname, "w") as fp:
        if not ESP32:
            recurse_dct(CONFIG, "src/root/sd/", "sd/")
            json.dump(CONFIG, fp, indent=4)
            recurse_dct(CONFIG, "sd/", "src/root/sd/")
        else:
            # micropython doesn't support indent
            json.dump(CONFIG, fp, separators=(",\n", ":\n"))


if ESP32:
    hardware_init()  # fix TMC2209 UART pins
    deploy_assets()  # ensure config exists
CONFIG = load_config()
