import asyncio
import os
import machine
import json

import logging
import ota

from control import bootlib, constants
from control.webapp import app

if constants.ESP32:
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
    logging.info("sleeping 10 seconds")
    asyncio.sleep(10)
    bootlib.mount_sd()


async def main_task():
    """runs web server"""
    logging.info("sleeping 8 seconds")
    await asyncio.sleep(8)
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
