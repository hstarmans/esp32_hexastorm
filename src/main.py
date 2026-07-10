import asyncio
import logging


from control import bootlib, constants
from control.webapp import app
from control.laserhead import LASERHEAD as lh

constants.CONFIG = constants.load_config()
logger = logging.getLogger(__name__)


async def network_manager():
    """
    Handles initial connection attempts with retry logic.
    """
    max_attempts = 6
    connected = False

    # Initial state red
    await lh.set_leds(red=True, green=False, blue=False)

    for attempt in range(max_attempts):
        logger.info(f"Network: Connection attempt {attempt + 1}/{max_attempts}")
        if await bootlib.connect_wifi():
            logger.info("Network: Connected successfully!")
            await bootlib.set_time()
            connected = True
            await lh.set_leds(red=False, green=True, blue=False)
            break
        await asyncio.sleep(10)  # Wait 10s between trials

    if not connected:
        logger.warning("Network: Could not connect within 60 seconds. Running offline.")


async def main():
    tasks = []

    # Start the Background Network Manager (LEDs, WiFi connection, Time)
    network_task = asyncio.create_task(network_manager())
    tasks.append(network_task)

    # start webrepl if configured
    if constants.CONFIG["webrepl"]["start"]:
        logger.info("Starting WebREPL mode...")
        bootlib.start_webrepl()

    # start WebServer
    if constants.CONFIG["webserver"]["start"]:
        logger.info("Main: Starting Webserver NOW...")
        # We gather the webserver (foreground) and network (background)
        tasks.append(app.start_server(port=5000, debug=False))

    if tasks:
        await asyncio.gather(*tasks)
    bootlib.mark_boot_successfull()


if constants.ESP32:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.error("Keyboard interrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
