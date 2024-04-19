import asyncio
import os
import machine

import bootlib
import constants
from webapp import app


async def boot_procedure(timeout=10):
    """called when board is booted

    Connection made to wifi or access point is created.
    If there is internet connection, machine time is updated.
    Machine will reset in timeout minutes

    timeout:  time in minutes machine resets
    """
    try:
        if bootlib.connect_wifi():
            bootlib.set_time()
        else:
            print(f"Cannot connect, rebooting in {timeout} minutes")
            asyncio.sleep(60 * timeout)
            machine.reset()
    except Exception:
        print("Boot failed, rebooting in 1 minutes")
        asyncio.sleep(60)
    # errors created by mount_sd cannot be captured
    # don't use on devices without sd
    asyncio.sleep(10)
    bootlib.mount_sd()


async def main_task():
    """runs web server"""
    server_task = asyncio.create_task(app.start_server(port=5000, debug=True))
    await asyncio.gather(server_task)


def main():
    try:
        asyncio.run(main_task())
    except Exception:
        pass


asyncio.run(boot_procedure())
# main()
