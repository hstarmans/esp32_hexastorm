import logging
import asyncio
from time import time
from random import randint

from .constants import CONFIG
from hexastorm.controller import Host, executor


class Laserhead:
    def __init__(self, debug=False):
        self.host = Host(micropython=True)
        self.logger = logging.getLogger(__name__)
        self.debug = debug
        if not debug:
            self.host.init_steppers()
        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._start = asyncio.Event()
        self.reset_state()
        self.reset_fpga()

    def reset_fpga(self):
        self.host.reset()

    def flash(self, filename):
        fname = CONFIG["fpga"]["storagefolder"] + f"/{filename}"
        self.host.flash_fpga(fname)

    def reset_state(self):
        job = {
            "currentline": 0,
            "totallines": 0,
            "printingtime": 0,
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
            "job": job,
            "components": components,
        }
        self._state = state

    def stop_print(self):
        self.logger.debug("Print is stopped.")
        self._stop.set()

    def pause_print(self):
        self.logger.debug("Print is paused.")
        self._pause.set()

    @executor
    def toggle_laser(self):
        laser = self.state["components"]["laser"]
        self.state["components"]["laser"] = laser = not laser
        self.logger.debug(f"Laser on is {laser}")
        if not self._debug:
            yield from self.host.enable_comp(laser0=laser)

    @executor
    def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        self.logger.debug(f"Change rotation state prism to {prism}")
        if not self._debug:
            yield from self.host.enable_comp(polygon=prism)

    @executor
    def move(self, vector):
        self.logger.debug(f"Moving vector {vector}")
        if not self._debug:
            yield from self.host.gotopoint(vector, absolute=False)

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
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.NOTSET)

    async def test_diode(self, timeout=3):
        self.logger.debug("Starting diode test")
        self.state["components"]["diodetest"] = None
        if self._debug:
            await asyncio.sleep(timeout)
            self.state["components"]["diodetest"] = (
                True if randint(0, 10) > 5 else False
            )
            return
        else:
            for res in self.host.get_state():
                pass
            if res["photodiode_trigger"] != 0:
                self.logger.error("Diode already triggered")
                self.state["components"]["diodetest"] = False
                return
            for _ in self.host.enable_comp(laser1=True, polygon=True):
                pass
            self.logger.debug(f"Wait for diode trigger, {timeout} seconds")
            await asyncio.sleep(timeout)
            for _ in self.host.enable_comp(laser1=False, polygon=False):
                pass
            for res in self.host.get_state():
                pass
            self.state["components"]["diodetest"] = res["photodiode_trigger"]
            if res == 0:
                self.logger.error("Diode not triggered")
            else:
                self.logger.debug("Diode test passed")

    async def print_loop(self, fname):
        self._stop.clear()
        self._pause.clear()
        # TODO: this would normally come from a file
        total_lines = 10
        self.reset_state()
        self.state["printing"] = True
        self.state["job"]["totallines"] = total_lines
        self.state["job"]["filename"] = fname
        start_time = time()
        for line in range(total_lines):
            if self._pause.is_set():
                self._pause.clear()
                while True:
                    await asyncio.sleep(2)
                    if self._stop.is_set() or self._pause.is_set():
                        self._pause.clear()
                        break
            if self._stop.is_set():
                self._stop.clear()
                break
            self.state["job"]["currentline"] = line + 1
            self.state["job"]["printingtime"] = round(time() - start_time)
            await asyncio.sleep(5)
        self.state["printing"] = False


LASERHEAD = Laserhead()
