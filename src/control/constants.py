import os

import json
import asyncio
import sys


ESP32 = False if sys.platform in ["linux", "win32"] else True

if ESP32:
    import control.frozen_root


def recurse_dct(dct, target, replace):
    for key, val in dct.items():
        if isinstance(val, dict):
            recurse_dct(dct, replace)
        elif isinstance(val, str):
            dct[key] = val.replace(target, replace)


def load_config():
    """load json config settings"""

    fname = "config.json" if ESP32 else "src/root/config.json"
    with open(fname) as f:
        dct = json.load(f)
        if not ESP32:
            recurse_dct(dct, "sd/", "src/root/sd/")
    return dct


def update_config():
    """update the json settings"""
    fname = "config.json" if ESP32 else "src/root/config.json"
    with open(fname, "w") as fp:
        if not ESP32:
            recurse_dct(CONFIG, "src/root/sd/", "sd/")
        json.dump(CONFIG, fp)


def state():
    "returns the default machine state"
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
