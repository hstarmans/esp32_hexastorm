import asyncio
import os
import json

import logging
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
    # connection status loop will try to reconnect
    # and set time in case of failure
    if bootlib.connect_wifi():
        await bootlib.set_time()
    # errors created by mount_sd cannot be captured
    # don't use on devices without sd
    logging.info("sleeping 1 seconds")
    await asyncio.sleep(1)
    bootlib.mount_sd()


async def main_task():
    """runs web server"""
    # creates laserhead which lifts reset pin, disables REPL
    from control.webapp import app

    logging.info("sleeping 5 seconds")
    await asyncio.sleep(5)
    server_task = asyncio.create_task(app.start_server(port=5000, debug=True))
    await asyncio.gather(server_task)


def main():
    try:
        asyncio.run(main_task())
    except KeyboardInterrupt:
        logging.error("Keyboard interrupt")
        pass


if constants.ESP32:
    asyncio.run(boot_procedure())
    bootlib.start_webrepl()
    # main()
