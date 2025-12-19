import asyncio
import os
import json
import logging
import network
from machine import Pin

import ota.rollback

from control import bootlib, constants
from control.webapp import app


logger = logging.getLogger(__name__)
bootlib.set_log_level(logging.INFO)
bootlib.check_crash_loop_rtc()

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
    tasks = []
    constants.CONFIG = constants.load_config()

    # We need the interface ON for the Webserver/WebREPL to bind ports.
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # CRITICAL: Immediately stop the background auto-connect process.
    # This prevents the "Wifi Internal Error" race condition later.
    wlan.disconnect()

    # Start the Background Network Manager (LEDs, WiFi connection, Time)

    network_task = asyncio.create_task(network_manager())
    tasks.append(network_task)

    # start webrepl if configured
    if constants.CONFIG["webrepl"]["start"]:
        logging.info("Starting WebREPL mode...")
        bootlib.start_webrepl()

    # start WebServer
    if constants.CONFIG["webserver"]["start"]:
        logging.info("Main: Starting Webserver NOW...")
        # We gather the webserver (foreground) and network (background)
        tasks.append(app.start_server(port=5000, debug=False))

    if tasks:
        await asyncio.gather(*tasks)
    bootlib.mark_boot_successfull()


if constants.ESP32:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.error("Keyboard interrupt")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
