import json
import asyncio
import sys


def get_mqtt_settings():
    """ "rerieves settings from json file"""
    with open("config.json") as f:
        dct = json.load(f)["mqtt"]
    return dct


def set_mqtt_settings(dct):
    """updates mqt settings and resets mqtt client

    returns True on success False otherwise
    """
    dct_old = get_mqtt_settings()
    try:
        # sanity checks
        assert sorted(dct_new.keys()) == sorted(dct_old.keys())
        for v in dct_old.values():
            assert len(v) > 0

        with open("config.json") as f:
            set_old = json.load(f)

        set_old["mqtt"] = dct
        constants.MQTT_SETTINGS = dct

        with open("config.json", "w") as fp:
            json.dump(set_old, fp)

        constants.MQTT_CLIENT = get_mqttclient()
        return True
    except (AssertionError, NameError, KeyError):
        return False


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


ESP32 = False if sys.platform == "linux" else True
REBOOT_MQTT = asyncio.Event()
MQTT_SETTINGS = get_mqtt_settings()
WIFI_SETTINGS = get_wifi_settings()
MEASUREMENT = [0, 0, 0, 0]
SENSORS = []

MACHINE_STATE = {
    "printing": False,
    "rotating": False,
    "laser": False,
    "diodetest": None,
    "filename": "no name",
    "currentline": 0,
    "totallines": 0,
    "printingtime": 10,
}
