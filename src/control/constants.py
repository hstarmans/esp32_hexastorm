import asyncio
import json
import logging
import sys

ESP32 = False if sys.platform in ["linux", "win32"] else False
logger = logging.getLogger(__name__)

if ESP32:
    import control.frozen_root

    logger.info(f"Executing {control.frozen_root}")


def recurse_dct(dct, target, replace):
    for key, val in dct.items():
        if isinstance(val, dict):
            recurse_dct(val, target, replace)
        elif isinstance(val, str):
            dct[key] = val.replace(target, replace)


def load_config():
    """Load json config settings."""
    fname = "config.json" if ESP32 else "src/root/config.json"
    with open(fname) as f:
        dct = json.load(f)
        if not ESP32:
            recurse_dct(dct, "sd/", "src/root/sd/")
    return dct


def update_config():
    """Update the json settings."""
    fname = "config.json" if ESP32 else "src/root/config.json"
    with open(fname, "w") as fp:
        if not ESP32:
            recurse_dct(CONFIG, "src/root/sd/", "sd/")
        json.dump(CONFIG, fp)
        if not ESP32:
            recurse_dct(CONFIG, "sd/", "src/root/sd/")


def state():
    """Return default machine state."""
    job = {
        "currentline": 0,
        "totallines": 0,
        "printingtime": 0,
        "filename": "no file name",
    }
    components = {
        "laser": False,
        "diodetest": None,
        "rotating": False,
    }
    wifi = {
        "available": [],
        "connected": False,
    }
    state = {
        "files": [],
        "printing": False,
        "job": job,
        "components": components,
        "wifi": wifi,
    }
    return state


STOP_PRINT = asyncio.Event()
PAUSE_PRINT = asyncio.Event()
CONFIG = load_config()
STATE = state()
