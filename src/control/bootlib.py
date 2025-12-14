import asyncio
from time import localtime, time

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
logger = logging.getLogger(__name__)


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
    logger.info(f"Total space: {total_mb:.2f} MB")
    logger.info(f"Used space: {used_mb:.2f} MB")
    logger.info(f"Free space: {free_mb:.2f} MB")
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
    logger.info(f"Local time before synchronization {localtime()}")
    if not is_connected():
        logger.info("Trying to connect to wifi connection")
        if not await connect_wifi():
            return
    for trial in range(tries):
        try:
            # make sure to have internet connection
            ntptime.settime()
            break
        except OSError:
            logger.info(f"Trial {trial + 1} out of {tries}")
            logger.info("Error syncing time, probably not connected")
            await asyncio.sleep(5)
    if localtime()[0] < 2024:
        logger.error("Failed updating time")
    logger.info(f"Local time after synchronization {localtime()}")


def set_log_level(level):
    """Sets the log level for the root logger.  Call this ONCE at the start."""
    if level is None:
        level = logging.INFO
    global _logging_configured

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not _logging_configured:
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
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
        logger.error("Cannot retrieve firmware url, probably wifi connection")
        return {}

    if r.status_code == 200:
        release_dct = json.loads(r.text)
    else:
        logger.error("Response for firmware url is invalid")
        return {}

    def clean(code):
        return int(code.replace(".", "").replace("v", ""))

    if (not require_new) or (clean(release_dct["tag_name"]) > clean(gh["version"])):
        return release_dct
    else:
        logger.info("No new firmware")
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

        with requests.get(release_dct["assets"][0]["url"], headers=head) as resp:
            if resp.status_code != 200:
                logger.error("Download firmware binary failed")
                return False
            else:
                with open(f"{gh['storagefolder']}/{gh['bin_name']}", mode="wb") as file:
                    for chunk in resp.iter_content(chunk_size=1024):
                        file.write(chunk)
        logger.info(f"Downloaded file {gh['bin_name']}")

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
        if not loop:
            pass
        elif led_on:
            await asyncio.sleep(on_time)
        else:
            await asyncio.sleep(off_time)
        led_on = not led_on
        if (time() - current_time) >= wifi_cycle_time:
            wifi_connected = is_connected()
            current_time = time()
            if not wifi_connected:
                await connect_wifi()
        if not loop:
            break


@wrapper_esp32(res=True)
async def connect_wifi(force=False):
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
        wlan.disconnect()
        await asyncio.sleep(0.5)  # Give the driver a moment to reset
        # method can fail due to power supply issues
        try:
            wlan.connect(wifi_login["ssid"], wifi_login["password"])
        except OSError as e:
            logger.error(f"WLAN Connect failed with error: {e}")
        max_wait = 10
        while max_wait > 0:
            if wlan.isconnected():
                made_connection = True
                break
            else:
                max_wait -= 1
                await asyncio.sleep(1)
        ap.active(False)
    else:
        made_connection = True
        ap.active(False)
    if made_connection:
        logger.info(f"Network config {wlan.ifconfig()}")
    else:
        wlan.active(False)
        logger.error("Cannot connect to wifi, creating access point!")
        logger.error(f"Used ssid {wifi_login['ssid']} and {wifi_login['password']}.")
        ap.active(True)
        ap.config(
            essid=f"sensor_serial{constants.CONFIG['serial']}",
            authmode=network.AUTH_WPA_WPA2_PSK,
            max_clients=10,
            password=wifi_login["ap_password"],
        )
    return made_connection


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


def check_crash_loop_rtc():
    """Prevents boot loops by tracking crashes in RTC memory (survives soft resets).

    If the crash count exceeds 3, the device enters a Safe Mode infinite loop
    instead of booting, allowing USB recovery. To reset manually, power cycle
    the device.
    """
    MAX_CRASHES = 3
    rtc = machine.RTC()

    # Read RTC memory (it returns bytes)
    data = rtc.memory()

    try:
        count = int(data)
    # If it's empty or garbage (first boot after power loss), reset to 0
    except (ValueError, TypeError):
        count = 0

    logger.info(f"Boot count (RTC): {count}")

    # Check Safety Limit
    if count >= MAX_CRASHES:
        logger.error("!!! DETECTED CRASH LOOP (RTC) !!!")
        logger.error("Stopping boot process to protect device.")
        logger.info("Connect via WebREPL or Serial (Ctrl+C to bypass if stuck).")
        RuntimeError("SAFE MODE ACTIVATED: Boot halted due to crash loop.")

    # Increment and Save back to RTC (No flash write!)
    rtc.memory(str(count + 1).encode())


def mark_boot_successfull():
    """Clear the crash counter"""
    logging.info("System stable. Clearing RTC crash counter.")
    machine.RTC().memory(b"")  # Clear the memory


@wrapper_esp32()
def mount_sd():
    """Mounts SDCard and change working directory."""
    try:
        sd = machine.SDCard(slot=2)
        os.mount(sd, "/sd")
    except OSError:
        logger.error(
            """Cannot connect to sdcard.\n"""
            """Hard reboot is required, not mounted"""
        )
