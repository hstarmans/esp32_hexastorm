import logging
import asyncio
from time import time
from random import randint

from ..constants import CONFIG


logger = logging.getLogger(__name__)


class BaseLaserhead:
    def __init__(self):
        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._start = asyncio.Event()
        self.statechange = asyncio.Event()
        self._debug = False
        self.reset_state()

    def reset_state(self):
        job = {
            "currentline": 0,
            "totallines": 0,
            "printingtime": 0,
            "exposureperline": 1,
            "singlefacet": False,
            "laserpower": 130,
            "filename": "no file name",
        }
        job.update(CONFIG["defaultprint"])
        components = {
            "laser": False,
            "diodetest": None,
            "rotating": False,
        }
        state = {
            "printing": False,
            "paused": False,
            "job": job,
            "components": components,
        }
        self._state = state

    def stop_print(self):
        logger.debug("Print is stopped.")
        self._stop.set()

    def pause_print(self):
        logger.debug("Print is paused.")
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()
        self.state["paused"] = self._pause.is_set()

    async def enable_comp(
        self,
        laser0=False,
        laser1=False,
        polygon=False,
        synchronize=False,
        singlefacet=False,
    ):
        """enable components

        laser0   -- True enables laser channel 0
        laser1   -- True enables laser channel 1
        polygon  -- False enables polygon motor
        """
        logger.debug(f"laser0, laser1, polygon set to {laser0, laser1, polygon}")
        self.state["components"]["laser"] = laser0 or laser1
        self.state["components"]["rotating"] = polygon
        self.state["job"]["singlefacet"] = singlefacet

    async def toggle_laser(self):
        laser = self.state["components"]["laser"]
        self.state["components"]["laser"] = laser = not laser
        logger.debug(f"Laser on is {laser}")

    async def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        logger.debug(f"Change rotation state prism to {prism}.")

    async def move(self, vector):
        logger.debug(f"Moving vector {vector}.")

    @property
    def state(self):
        return self._state

    @property
    def debug(self):
        return self._debug

    @debug.setter
    def debug(self, value):
        self._debug = value
        if value:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.NOTSET)

    async def notify_listeners(self):
        self.statechange.set()
        # Yield CPU for 1 cycle to let all web clients wake up and process the 'set' state
        await asyncio.sleep(0)
        # Now clear it so they wait for the next one
        self.statechange.clear()

    async def test_diode(self, timeout=3):
        logger.debug("Starting diode test.")
        self.state["components"]["diodetest"] = None
        await self.notify_listeners()
        await asyncio.sleep(timeout)
        self.state["components"]["diodetest"] = True if randint(0, 10) > 5 else False
        await self.notify_listeners()

    async def handle_pausing_and_stopping(self):
        if self._pause.is_set():
            while self._pause.is_set() and not self._stop.is_set():
                await asyncio.sleep(2)
                logger.debug("Printing paused")
            logger.debug("Printing resumed")
        if self._stop.is_set():
            return True
        return False

    async def print_loop_prep(self, fname):
        self._stop.clear()
        self._pause.clear()
        self.reset_state()
        self.state["printing"] = True
        self.state["job"]["filename"] = fname
        self.state["job"]["laserpower"] = CONFIG["defaultprint"]["laserpower"]
        exposures = self.state["job"]["exposureperline"] = CONFIG["defaultprint"][
            "exposureperline"
        ]
        self.state["job"]["singlefacet"] = CONFIG["defaultprint"]["singlefacet"]
        basestring = (
            f"Printing with laserpower {self.state['job']['laserpower']}"
            f" and {exposures} exposures, "
        )
        if self.state["job"]["singlefacet"]:
            basestring += "using a single facet."
        else:
            basestring += "without using a single facet."
        logger.info(basestring)

    async def print_loop(self, fname):
        await self.print_loop_prep(fname)
        # TODO: this would normally come from a file
        total_lines = 10

        self.state["job"]["totallines"] = total_lines
        start_time = time()
        for line in range(total_lines):
            logger.info(f"Exposing line {line}.")
            if await self.handle_pausing_and_stopping():
                break
            self.state["job"]["currentline"] = line + 1
            self.state["job"]["printingtime"] = round(time() - start_time)
            await asyncio.sleep(5)
        self.state["printing"] = False
