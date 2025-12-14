import json
import logging
import sys

ESP32 = False if sys.platform in ["linux", "win32", "darwin"] else True
logger = logging.getLogger(__name__)


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
            json.dump(CONFIG, fp, indent=4)
            recurse_dct(CONFIG, "sd/", "src/root/sd/")
        else:
            # micropython doesn't support indent
            json.dump(CONFIG, fp, separators=(",\n", ":\n"))


CONFIG = load_config()
