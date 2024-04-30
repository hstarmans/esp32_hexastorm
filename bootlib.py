import os
from time import sleep, localtime
import requests
import json


import machine
import ntptime

import constants

if constants.ESP32:
    import network
    import esp
    import webrepl
    from ota.update import OTA


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
    wifi_login = constants.WIFI_SETTINGS
    webrepl.start(password=wifi_login["webrepl_password"])


@wrapper_esp32()
def set_time(tries=3):
    """updates local time"""
    print(f"Local time before synchronization {localtime()}")
    for trial in range(tries):
        try:
            # make sure to have internet connection
            ntptime.settime()
            break
        except OSError:
            print(f"Trial {trial + 1} out of {tries}")
            print("Error syncing time, probably not connected")
            sleep(1)
    print(f"Local time after synchronization {localtime()}")


@wrapper_esp32(res=["connected", "otheroption"])
def list_wlans():
    """ "retrieves list of available wireless networks"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    results = []
    for ssid in wlan.scan():
        name = ssid[0].decode()
        if len(name) > 0:
            results.append(name)
    return results


@wrapper_esp32()
def update_firmware():
    # TODO: make it an async method
    # more information see https://realpython.com/python-download-file-from-url/
    user = "hstarmans"
    repo = "esp32_hexastorm"
    branch = "main"
    release_folder = "releases"
    # token should be of form "github_pat"
    token = None
    if token:
        head = {"Authorization": f"token {token}"}
    else:
        head = None

    version_url = (
        f"https://github.com/{user}/{repo}/raw/{branch}/{release_folder}/version.json"
    )
    r = requests.get(version_url, headers=head)
    release_dct = json.loads(r.text)

    if release_dct["release"] <= constants.get_key_json("version"):
        print("No new release")
        return False

    bin_name = release_dct["firmware"]

    release_url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{release_folder}/{bin_name}"
    with requests.get(release_url, headers=head, stream=True) as response:
        if not response.ok:
            print("New version, cannot download firmware")
            return False
        else:
            with open(bin_name, mode="wb") as file:
                for chunk in response.iter_content(chunk_size=1024):
                    file.write(chunk)

    # Write firmware from a url or filename
    # reboot if successful and verified
    with OTA(reboot=True) as ota:
        ota.from_firmware_file(
            bin_name,
            sha=release_dct["sha"],
            length=release_dct["length"],
        )


@wrapper_esp32(res=True)
def is_connected():
    """True is connected to a wireless network"""
    if constants.ESP32:
        wlan = network.WLAN(network.STA_IF)
        return wlan.isconnected()
    else:
        print("Network only supported on ESP32")
        return True


@wrapper_esp32(res=True)
def connect_wifi():
    """tries to connect to wifi

    If connection fails access point is created
    If connection succeeds active access points are deactivated

    returns boolean: True if connected
    """
    made_connection = False
    wlan = network.WLAN(network.STA_IF)
    ap = network.WLAN(network.AP_IF)
    wlan.active(True)
    wifi_login = constants.WIFI_SETTINGS
    if not wlan.isconnected():
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
        print("Network config:", wlan.ifconfig())
    else:
        wlan.active(False)
        print("Cannot connect to wifi, creating access point!")
        ap.active(True)
        ap.config(
            essid=wifi_login["essid"],
            authmode=network.AUTH_WPA_WPA2_PSK,
            max_clients=10,
            password=wifi_login["webrepl_password"],
        )
    return made_connection


@wrapper_esp32()
def mount_sd():
    """mounts SDCard and changes working directory"""
    try:
        os.listdir("sd")
    except OSError:
        # directory does not exist try mounting
        try:
            sd = machine.SDCard(slot=2)
            os.mount(sd, "/sd")
        except OSError:
            print(
                """Cannot connect to sdcard.\n"""
                """Hard reboot is required, not mounted"""
            )
