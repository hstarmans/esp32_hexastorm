import asyncio
from time import localtime

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


@wrapper_esp32(res=False)
async def update_firmware(force=False):
    """
    Downloads firmware from GitHub Releases and streams it directly to the
    ESP32 OTA partition. Executes asynchronously to keep hardware loops alive.
    """
    gh = constants.CONFIG["github"]

    # 1. Check for release (Blocking call, but very fast)
    release_dct = get_firmware_dct(require_new=(not force))
    if not release_dct:
        return False

    try:
        asset_url = release_dct["assets"][0]["url"]
        asset_size = release_dct["assets"][0]["size"]
    except (KeyError, IndexError):
        logger.error("Release JSON is missing asset data.")
        return False

    head = {
        "User-Agent": f"laserhead {constants.CONFIG['serial']}",
        "Accept": "application/octet-stream",
    }
    if len(gh["token"]) > 0:
        head["Authorization"] = f"token {gh['token']}"

    logger.warning(
        f"Starting direct-to-flash OTA update ({asset_size / 1024 / 1024:.2f} MB)..."
    )

    if constants.ESP32:
        try:
            # stream=True ensures we don't load the whole file into RAM
            with requests.get(asset_url, headers=head, stream=True) as resp:
                if resp.status_code not in (200, 302):
                    logger.error(f"Download failed with status code {resp.status_code}")
                    return False

                # We want to clean up files BEFORE we reboot.
                from ota.update import OTA

                with OTA() as ota:
                    downloaded = 0

                    # 4096 bytes perfectly matches an ESP32 flash memory page
                    for chunk in resp.iter_content(chunk_size=4096):
                        if not chunk:
                            break

                        ota.write(chunk)
                        downloaded += len(chunk)

                        # Yield control back to the async loop!
                        # This keeps your webserver responding and prevents watchdog crashes.
                        await asyncio.sleep(0)

                    if downloaded < asset_size:
                        logger.error(
                            f"Incomplete download! Got {downloaded}/{asset_size} bytes."
                        )
                        return False

            logger.info("OTA Flash successful! Validating and finalizing partition...")

            # 3. Clean up the file system before reboot
            logger.info("Purging old frozen assets...")
            for fld in ["templates", "static"]:
                try:
                    for f in os.listdir(fld):
                        os.remove(f"{fld}/{f}")
                    os.rmdir(fld)
                except OSError:
                    pass

            try:
                os.rename("config.json", "config_old.json")
            except OSError:
                pass

            # 4. Trigger the reboot safely
            logger.warning(
                "Update applied! Rebooting into new firmware in 2 seconds..."
            )
            await asyncio.sleep(2)
            import machine

            machine.reset()

        except Exception as e:
            logger.error(f"OTA Update crashed: {e}")
            return False

    else:
        # Mock behavior for PC
        logger.info(
            f"Mock OTA: Simulated direct-to-flash of {asset_url} ({asset_size} bytes)"
        )
        return True


@wrapper_esp32(res=False)
async def connect_wifi(force=False, create_ap=True):
    """
    Tries to connect to WiFi.

    :param bool force: Force reconnection even if already connected.
    :param bool create_ap: If True and WiFi fails, starts Access Point mode.

    :return bool: True if connected to WiFi, False otherwise.
    """
    made_connection = False
    wlan = network.WLAN(network.STA_IF)
    ap = network.WLAN(network.AP_IF)
    wlan.active(True)
    wlan.config(reconnects=False)

    wifi_login = constants.CONFIG.get("wifi_login", {})

    if not wlan.isconnected() or force:
        if wifi_login.get("static_enabled", False):
            wlan.ifconfig(
                (
                    wifi_login.get("static_ip"),
                    wifi_login.get("dnsmask"),
                    wifi_login.get("gateway_ip"),
                    wifi_login.get("primary_dns"),
                )
            )
        wlan.disconnect()
        await asyncio.sleep(0.5)

        try:
            wlan.connect(wifi_login.get("ssid", ""), wifi_login.get("password", ""))
        except OSError as e:
            logger.error(f"WLAN Connect failed with error: {e}")

        max_wait = 10
        while max_wait > 0:
            if wlan.status() == network.STAT_WRONG_PASSWORD:
                logger.error("Wrong WiFi password!")
                break
            if wlan.isconnected():
                made_connection = True
                break
            max_wait -= 1
            await asyncio.sleep(1)

    else:
        made_connection = True

    if made_connection:
        ap.active(False)
        logger.info(f"Network config {wlan.ifconfig()}")
    elif create_ap:
        wlan.active(False)
        logger.error("Cannot connect to WiFi, creating Access Point...")

        try:
            # Fallbacks in case config keys are missing or invalid
            ap_essid = str(wifi_login.get("essid", "hexastorm"))
            ap_pass = str(wifi_login.get("ap_password", "hexastorm"))

            # Ensure password meets 8-character minimum for WPA2
            if len(ap_pass) < 8:
                ap_pass = ap_pass.ljust(8, "0")

            # Configure BEFORE activating
            ap.config(
                essid=ap_essid,
                authmode=network.AUTH_WPA_WPA2_PSK,
                max_clients=10,
                password=ap_pass,
            )
            ap.active(True)
            logger.info(f"Access Point active: essid {ap_essid}")
        except Exception as e:
            logger.error(f"Failed to start Access Point: {e}")

    return made_connection


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
