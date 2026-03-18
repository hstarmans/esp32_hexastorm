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
        logger.info("Print is stopped.")
        self._stop.set()

    def pause_print(self):
        logger.info("Print is paused.")
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
        logger.info(f"Laser on is {laser}")

    async def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        logger.info(f"Change rotation state prism to {prism}.")

    async def move(self, vector):
        logger.info(f"Moving vector {vector}.")

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

    async def test_diode(self):
        logger.debug("Starting diode test (Mock - Fixed Reports).")
        self.state["components"]["diodetest"] = None
        expected_rpm = 3000
        num_facets = 4

        # Calculate ideal timing
        exp_facet_ms = 60 / (expected_rpm * num_facets / 1000)

        await self.notify_listeners()

        # Fixed "Golden Unit" Pass Report
        pass_report = {
            "passed": True,
            "global_mean_ms": round(exp_facet_ms, 4),
            "global_deviation_perc": 0.02,
            "expected_rpm": expected_rpm,
            "measured_rpm": expected_rpm,
            "facets": {
                0: {
                    "passed": True,
                    "mean_ms": round(exp_facet_ms + 0.001, 4),
                    "jitter_perc": 0.0680,
                    "samples_used": 96,
                },
                1: {
                    "passed": True,
                    "mean_ms": round(exp_facet_ms + 0.002, 4),
                    "jitter_perc": 0.0025,
                    "samples_used": 96,
                },
                2: {
                    "passed": True,
                    "mean_ms": round(exp_facet_ms - 0.001, 4),
                    "jitter_perc": 0.0042,
                    "samples_used": 96,
                },
                3: {
                    "passed": True,
                    "mean_ms": round(exp_facet_ms - 0.002, 4),
                    "jitter_perc": 0.1361,
                    "samples_used": 96,
                },
            },
        }

        # Fixed Fail Report
        # If RPM is 10 higher, the actual mean_ms goes down slightly.
        fail_rpm = expected_rpm + 10
        fail_mean_ms = 60 / (fail_rpm * num_facets / 1000)

        fail_report = {
            "passed": False,
            "global_mean_ms": round(fail_mean_ms, 4),
            "global_deviation_perc": 0.04,
            "expected_rpm": expected_rpm,
            "measured_rpm": fail_rpm,
            "facets": {
                0: {
                    "passed": True,
                    "mean_ms": round(fail_mean_ms + 0.001, 4),
                    "jitter_perc": 0.0680,
                    "samples_used": 96,
                },
                1: {
                    "passed": True,
                    "mean_ms": round(fail_mean_ms + 0.002, 4),
                    "jitter_perc": 0.0025,
                    "samples_used": 96,
                },
                2: {
                    "passed": True,
                    "mean_ms": round(fail_mean_ms - 0.001, 4),
                    "jitter_perc": 0.0042,
                    "samples_used": 96,
                },
                3: {
                    "passed": False,
                    "mean_ms": round(fail_mean_ms - 0.004, 4),
                    "jitter_perc": 0.2161,  # Failed jitter
                    "samples_used": 96,
                },
            },
        }

        # 50/50 chance to serve the pass or fail report
        report = pass_report if randint(0, 1) == 1 else fail_report

        self.state["components"]["diodetest"] = report
        await self.notify_listeners()
        logger.debug(f"Diode test (Mock) finished. Passed: {report['passed']}")

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
            await self.notify_listeners()
            await asyncio.sleep(5)
        self.state["printing"] = False
        await self.notify_listeners()
