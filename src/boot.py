import asyncio
import os
import json
import logging

from machine import Pin
import ota.rollback

from control import bootlib, constants

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

logger = logging.getLogger(__name__)


async def boot_procedure():
    """called when board is booted

    Connection made to wifi or access point is created.
    If there is internet connection, machine time is updated.
    Machine will reset in timeout minutes
    """
    # UART RX and TX connected via resistor
    # and TMC2209, which results in endless
    # communication and failure of micropython shell
    # fix is to set UART1 to zero
    pin43 = Pin(43, Pin.OUT)
    pin43.value(0)
    pin44 = Pin(44, Pin.OUT)
    pin44.value(0)
    ##
    bootlib.set_log_level(logging.INFO)
    # sleep allows you to exit a boot loop with CTRL+C
    logging.info("sleeping 2 seconds to allow for CTRL+C")
    await asyncio.sleep(2)

    # connection status loop will try to reconnect
    # and set time in case of failure
    if bootlib.connect_wifi():
        await bootlib.set_time()

    # https://github.com/thonny/thonny/issues/2624
    import network

    ap_if = network.WLAN(network.AP_IF)
    ap_if.active(False)

    # show status
    asyncio.run(bootlib.status_loop(loop=False))


async def main_task():
    """runs web server"""
    # creates laserhead which lifts reset pin, disables REPL
    from control.webapp import app
    
    server_task = asyncio.create_task(app.start_server(port=5000, debug=False))
    logging.info("Launching webserver.")
    bootlib.connect_wifi() # to print settings
    await asyncio.gather(server_task)


if constants.ESP32:
    try:
        asyncio.run(boot_procedure())
        if constants.CONFIG["webrepl"]["start"]:
            bootlib.start_webrepl()
        else:
            logging.info("Webrepl not started")
        if constants.CONFIG["webserver"]["start"]:
            asyncio.run(main_task())
        else:
            logging.info("Webserver not started")
    # requires micropython shell connection via USB not webrepl
    except KeyboardInterrupt:
        logging.error("Keyboard interrupt")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")

