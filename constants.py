import json
import asyncio
import sys


def get_key_json(key):
    """retrieve key from json file"""
    with open("config.json") as f:
        dct = json.load(f)[key]
    return dct


def change_key_json(key, value):
    """write value to key in json file"""
    # store settings in config
    with open("config.json") as f:
        set_old = json.load(f)
    set_old[key] = value
    with open("config.json", "w") as fp:
        json.dump(set_old, fp)


def init_state():
    "returns the default machine state"
    job = {
        "passesperline": 0,
        "laserpower": 70,
        "currentline": 0,
        "totallines": 0,
        "printingtime": 0,
        "filename": "no file name",
    }
    control = {
        "laser": False,
        "diodetest": None,
        "rotating": False,
    }
    wifi = {
        "available": [],
        "connected": False,
    }
    wifi.update(WIFI_SETTINGS)
    state = {
        "files": [],
        "printing": False,
        "job": job,
        "control": control,
        "wifi": wifi,
    }
    return state


ESP32 = False if sys.platform == "linux" else True
STOP_PRINT = asyncio.Event()
PAUSE_PRINT = asyncio.Event()
WIFI_SETTINGS = get_key_json("wifi_login")
# MAX allowable request size
#   Needed to enable 10 Mb uploads
MAX_CONTENT_LENGTH = 10 * 1024 * 1024


# salt or secret key
# Generate via import uuid // uiid.uuid4()
SECRET_KEY = "76081385-7eb7-411d-966f-712f1a15500"
# key is "wachtwoord"
PASSWORD = "6bcad2870a09b584d9045736c372085c58a871bf4ec1333f128e2f7c37d2806e"
UPLOAD_FOLDER = "sd/files"


MACHINE_STATE = init_state()
