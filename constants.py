import json
import asyncio
import sys


def get_wifi_settings():
    """retrieve settings from json file"""
    with open("config.json") as f:
        dct = json.load(f)["wifi_login"]
    # boolean conversion requires lower case
    dct["static_enabled"] = json.loads(dct["static_enabled"])
    return dct


def update_wifi_settings(dct_new):
    """updates wifi settings and reconnects wifi

    returns True on success False otherwise
    """
    # if you change the wifi settings you need to
    # update mqtt client as well
    dct_old = get_wifi_settings()
    try:
        # sanity checks
        assert sorted(dct_new.keys()) == sorted(dct_old.keys())
        for v in dct_old.values():
            assert len(v) > 0

        # store settings in config
        with open("config.json") as f:
            set_old = json.load(f)

        set_old["wifi_login"] = dct_new

        with open("config.json", "w") as fp:
            json.dump(set_old, fp)

        constants.WIFI_SETTINGS = dct_new
        connect_wifi()
        return True
    except (AssertionError, NameError, KeyError):
        return False


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
WIFI_SETTINGS = get_wifi_settings()
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
