import asyncio
import os
import json
import logging
import network
import machine
import sys

from machine import Pin
import ota.rollback

from control import bootlib, constants
from control.webapp import app


def deploy_assets():
    """Extracts frozen assets only if a sentinel file is missing.

    Checks for '/templates/index.html' to skip redundant extraction on
    subsequent boots, preventing overwrite errors and reducing startup time.
    """

    # Import the module (now it does nothing but load definitions)
    import frozen_root

    # Pick a file that SHOULD exist if deployment worked
    try:
        os.stat("/templates/home.html")
        # If we get here, file exists. Skip extraction.
        logging.info("Assets already deployed. Skipping extraction.")
        return
    except OSError:
        # File doesn't exist (First boot or wipe)
        pass

    # Extract if missing
    logging.info("First boot detected. Extracting static files...")

    # We set overwrite=True here to ensure clean deployment
    # frozen_root.extract(target_path, overwrite=bool)
    frozen_root.extract("/", overwrite=True)

    logging.info("Asset deployment complete.")


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
        logger.info("!!! DETECTED CRASH LOOP (RTC) !!!")
        logger.info("Stopping boot process to protect device.")
        logger.info("You can now connect via WebREPL or Serial.")
        logger.info("To clear this state: UNPLUG the device power.")
        sys.exit()

    # Increment and Save back to RTC (No flash write!)
    rtc.memory(str(count + 1).encode())


async def mark_boot_successful():
    """If we stay alive for 10 seconds, clear the crash counter"""
    await asyncio.sleep(10)
    print("System stable. Clearing RTC crash counter.")
    machine.RTC().memory(b"")  # Clear the memory


logger = logging.getLogger(__name__)
bootlib.set_log_level(logging.INFO)
check_crash_loop_rtc()

# not supported on current module
if constants.ESP32 and False:
    # bootloader needs to keep booting
    # from this partition
    ota.rollback.cancel()
    try:
        with open("config_old.json") as f:
            dct2 = json.load(f)
        constants.CONFIG.update(dct2)
        constants.update_json()
        os.remove("config_old.json")
    except OSError:
        pass


async def hardware_init():
    """Fast hardware fixes run immediately on boot"""
    # UART RX and TX connected via resistor
    # and TMC2209, which results in endless
    # communication and failure of micropython shell
    # fix is to set UART1 to zero
    Pin(43, Pin.OUT).value(0)
    Pin(44, Pin.OUT).value(0)


async def network_manager():
    """
    Connects to WiFi
    Runs Status LED loop
    """
    # runs in background while webserver serves pages!
    if await bootlib.connect_wifi():
        await bootlib.set_time()

    # Start the status LED loop
    await bootlib.status_loop(loop=False)


async def main():
    await hardware_init()
    asyncio.create_task(mark_boot_successful())
    deploy_assets()
    # wlan needs to be on before starting webserver
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # Start the Background Network Manager (LEDs, WiFi connection, Time)
    network_task = asyncio.create_task(network_manager())

    # start webrepl if configured
    if constants.CONFIG["webrepl"]["start"]:
        logging.info("Starting WebREPL mode...")
        bootlib.start_webrepl()

    # start WebServer
    if constants.CONFIG["webserver"]["start"]:
        logging.info("Main: Starting Webserver NOW...")
        # We gather the webserver (foreground) and network (background)
        await asyncio.gather(app.start_server(port=5000, debug=False), network_task)


if constants.ESP32:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.error("Keyboard interrupt")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
