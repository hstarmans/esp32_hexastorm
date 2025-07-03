import asyncio
from time import sleep, localtime, time
import sys

try:
    from mrequests import urequests as requests
except ImportError:
    import requests
import json
import os
import logging

from . import constants
import machine

if constants.ESP32:
    import ntptime
    import network
    import esp
    import webrepl
    from ota.update import OTA

    from hexastorm.config import PlatformConfig

_logging_configured = False


def wrapper_esp32(res=None):
    def decorator(func):
        if not constants.ESP32:

            def wrapper(*args, **kwargs):
                print(f"{func.__name__} not supported")
                return res

        else:

            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def reload(module):
    """Reload a module in micropython.

    Allows to hot reload modules.
    Hot reloading is challenging. You need
    to update all scopes, run
    import module again, to update micropython shell scope.
    """
    module_name = module.__name__
    if module_name in sys.modules:
        del sys.modules[module_name]
    # Use the standard import statement
    globals()[module_name] = __import__(module_name)
    return globals()[module_name]


def disk_usage():
    """Compute disk usage

    prints disk usages and returns total, used and free bytes
    """
    statvfs = os.statvfs("/")  # Get file system statistics
    # statvfs[0] is block size
    total_mb = (statvfs[2] * statvfs[0]) / (1024 * 1024)
    free_mb = (statvfs[3] * statvfs[0]) / (1024 * 1024)
    used_mb = total_mb - free_mb
    # Print the results in a user-friendly format
    print(f"Total space: {total_mb:.2f} MB")
    print(f"Used space: {used_mb:.2f} MB")
    print(f"Free space: {free_mb:.2f} MB")
    return total_mb, used_mb, free_mb


@wrapper_esp32()
def start_webrepl():
    """start webrepl"""
    # disable debug output
    # recommended when using webrepl
    esp.osdebug(None)
    # TODO:
    # ideal procedure is first running webrepl_setup
    # password is stored not hashed, this is not ideal
    # webrepl.start()
    webrepl.start(password=constants.CONFIG["webrepl"]["webrepl_password"])


@wrapper_esp32()
async def set_time(tries=3):
    """Update local time."""
    logging.info(f"Local time before synchronization {localtime()}")
    if not is_connected():
        logging.info("Trying to connect to wifi connection")
        if not connect_wifi():
            return
    for trial in range(tries):
        try:
            # make sure to have internet connection
            ntptime.settime()
            break
        except OSError:
            logging.info(f"Trial {trial + 1} out of {tries}")
            logging.info("Error syncing time, probably not connected")
            await asyncio.sleep(5)
    if localtime()[0] < 2024:
        logging.error("Failed updating time")
    logging.info(f"Local time after synchronization {localtime()}")


def set_log_level(level):
    """Sets the log level for the root logger.  Call this ONCE at the start."""
    if level is None:
        level = logging.INFO
    global _logging_configured

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if not _logging_configured:
        # Create a handler to direct logs (usually to the console/stdout)
        handler = logging.StreamHandler()
        # streamhandler does not support filename and lineno
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        _logging_configured = True


@wrapper_esp32(res=["connected", "otheroption"])
def list_wlans():
    """Retrieves list of available wireless networks."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    results = []
    for ssid in wlan.scan():
        name = ssid[0].decode()
        if len(name) > 0:
            results.append(name)
    return results


@wrapper_esp32(res=True)
def is_connected():
    """True is connected to a wireless network"""
    wlan = network.WLAN(network.STA_IF)
    return wlan.isconnected()


def get_firmware_dct(require_new=True):
    gh = constants.CONFIG["github"]
    head = {
        "User-Agent": f"laserhead {constants.CONFIG['serial']}",
    }
    if len(gh["token"]) > 0:
        head["Authorization"] = f"token {gh['token']}"

    url = f"https://api.github.com/repos/{gh['user']}/{gh['repo']}/releases/latest"

    try:
        r = requests.get(url, headers=head)
    except OSError:
        logging.error("Cannot retrieve firmware url, probably wifi connection")
        return {}

    if r.status_code == 200:
        release_dct = json.loads(r.text)
    else:
        logging.error("Response for firmware url is invalid")
        return {}

    def clean(code):
        return int(code.replace(".", "").replace("v", ""))

    if (not require_new) or (
        clean(release_dct["tag_name"]) > clean(gh["version"])
    ):
        return release_dct
    else:
        logging.info("No new firmware")
        return {}


def update_firmware(force=False, download=True):
    gh = constants.CONFIG["github"]
    if download:
        release_dct = get_firmware_dct(require_new=(not force))
        if not release_dct:
            return False
        head = {
            "User-Agent": f"laserhead {constants.CONFIG['serial']}",
            "Accept": "application/octet-stream",
        }

        if len(gh["token"]) > 0:
            head["Authorization"] = f"token {gh['token']}"

        with requests.get(
            release_dct["assets"][0]["url"], headers=head
        ) as resp:
            if resp.status_code != 200:
                logging.error("Download firmware binary failed")
                return False
            else:
                with open(
                    f"{gh['storagefolder']}/{gh['bin_name']}", mode="wb"
                ) as file:
                    for chunk in resp.iter_content(chunk_size=1024):
                        file.write(chunk)
        logging.info(f"Downloaded file {gh['bin_name']}")

    if constants.ESP32:
        # purge templates and static folder
        flds = ["templates", "static"]
        for fld in flds:
            for f in os.listdir(fld):
                os.remove(fld + "/" + f)
        try:
            os.rename("config.json", "config_old.json")
        except OSError:
            pass
        # Write firmware from a url or filename
        # reboot if successful and verified
        with OTA(reboot=True) as ota:
            ota.from_firmware_file(
                f"{gh['storagefolder']}/{gh['bin_name']}",
                sha="",
                length=release_dct["assets"][0]["size"],
            )


@wrapper_esp32()
async def status_loop(loop=False):
    """display connection status via onboard led

    tries to reconnect and update time
    pulses a led
    red led no wifi, blue led is wifi
    """
    on_time = 2
    off_time = 6
    wifi_cycle_time = 60
    led_on = True
    Pin = machine.Pin
    wifi_connected = is_connected()
    leds = PlatformConfig(test=False).esp32_cfg["leds"]
    blue_led = Pin(leds["blue"], Pin.OUT)
    red_led = Pin(leds["red"], Pin.OUT)
    current_time = time()
    while True:
        if wifi_connected:
            red_led.value(1)
            blue_led.value(not led_on)
        else:
            red_led.value(not led_on)
            blue_led.value(1)
        # check year is greater than 2024
        if (localtime()[0] < 2024) and wifi_connected:
            await set_time(1)
        if led_on:
            await asyncio.sleep(on_time)
        else:
            await asyncio.sleep(off_time)
        led_on = not led_on
        if (time() - current_time) >= wifi_cycle_time:
            wifi_connected = is_connected()
            current_time = time()
            if not wifi_connected:
                connect_wifi()
        if not loop:
            break


@wrapper_esp32(res=True)
def connect_wifi(force=False):
    """tries to connect to wifi

    If connection fails access point is created
    If connection succeeds active access points are deactivated

    returns boolean: True if connected
    """
    made_connection = False
    wlan = network.WLAN(network.STA_IF)
    ap = network.WLAN(network.AP_IF)
    wlan.active(True)
    wifi_login = constants.CONFIG["wifi_login"]
    if not wlan.isconnected() or force:
        # if machine.reset_cause() != machine.SOFT_RESET:
        if wifi_login["static_enabled"]:
            wlan.ifconfig(
                (
                    wifi_login["static_ip"],
                    wifi_login["dnsmask"],
                    wifi_login["gateway_ip"],
                    wifi_login["primary_dns"],
                )
            )
        # method can fail due to power supply issues
        wlan.connect(wifi_login["ssid"], wifi_login["password"])
        max_wait = 10
        while max_wait > 0:
            if wlan.isconnected():
                made_connection = True
                break
            else:
                max_wait -= 1
                sleep(1)
        ap.active(False)
    else:
        made_connection = True
        ap.active(False)
    if made_connection:
        logging.info(f"Network config {wlan.ifconfig()}")
    else:
        wlan.active(False)
        logging.error("Cannot connect to wifi, creating access point!")
        logging.error(
            f"Used ssid {wifi_login['ssid']} and {wifi_login['password']}."
        )
        ap.active(True)
        ap.config(
            essid=f"sensor_serial{constants.CONFIG['serial']}",
            authmode=network.AUTH_WPA_WPA2_PSK,
            max_clients=10,
            password=wifi_login["ap_password"],
        )
    return made_connection


@wrapper_esp32()
def mount_sd():
    """Mounts SDCard and change working directory."""
    # removed as I use old esp32 for testing
    # try:
    #     os.listdir("sd")
    # except OSError:
    #     # directory does not exist try mounting
    try:
        sd = machine.SDCard(slot=2)
        os.mount(sd, "/sd")
        from . import frozen_root

        logging.info(f"executing {frozen_root}")
    except OSError:
        logging.error(
            """Cannot connect to sdcard.\n"""
            """Hard reboot is required, not mounted"""
        )
