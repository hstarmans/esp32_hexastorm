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
    asyncio.create_task(bootlib.mark_boot_successful())
    bootlib.deploy_assets()
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
