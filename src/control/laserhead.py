import logging
import asyncio
import struct
from time import time
from random import randint

from .constants import CONFIG 
from hexastorm.constants import wordsinscanline
from hexastorm.controller import Host
from hexastorm.controller import executor as exe


class Laserhead:
    def __init__(self, debug=False):
        self.host = Host(micropython=True)
        self.logger = logging.getLogger(__name__)
        self.debug = debug
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
            "exposureperline": 1,
            "laserpower": 70,
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

    def laser_current(self, val):
        """sets maximum laser current of laser driver per channel
        """
        self.host.laser_current(val)

    @exe
    def write_line(self, bitlst, stepsperline=1, direction=0, repetitions=1):
        yield from self.host.writeline(bitlst, stepsperline, direction, repetitions)

    @exe
    def enable_comp(
        self, laser0=False, laser1=False, polygon=False,
    ):
        """enable components

        laser0   -- True enables laser channel 0
        laser1   -- True enables laser channel 1
        polygon  -- False enables polygon motor
        """
        self.logger.debug(f"laser0, laser1, polygon set to {laser0, laser1, polygon}")
        if not self._debug:
            yield from self.host.enable_comp(laser0=laser0, laser1=laser1, polygon=polygon)
            self.state["components"]["laser"]  = laser0 or laser1
            self.state["components"]["rotating"]  = polygon

    @exe
    def toggle_laser(self):
        laser = self.state["components"]["laser"]
        self.state["components"]["laser"] = laser = not laser
        self.logger.debug(f"Laser on is {laser}")
        if not self._debug:
            yield from self.host.enable_comp(laser0=laser)

    @exe
    def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        self.logger.debug(f"Change rotation state prism to {prism}.")
        if not self._debug:
            yield from self.host.enable_comp(polygon=prism)

    @exe
    def move(self, vector):
        self.logger.debug(f"Moving vector {vector}.")
        if not self._debug:
            self.host.enable_steppers = True
            yield from self.host.gotopoint(vector, absolute=False)
            self.host.enable_steppers = False

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
        self.logger.debug("Starting diode test.")
        self.state["components"]["diodetest"] = None
        if self._debug:
            await asyncio.sleep(timeout)
            self.state["components"]["diodetest"] = (
                True if randint(0, 10) > 5 else False
            )
        else:
            host_state = exe(self.host.get_state)()
            if host_state["photodiode_trigger"] != 0:
                self.logger.error("Diode already triggered.")
                self.state["components"]["diodetest"] = False
            else:
                exe(lambda: self.host.enable_comp(laser1=True, polygon=True))()
                self.logger.debug(f"Wait for diode trigger, {timeout} seconds.")
                await asyncio.sleep(timeout)
                exe(lambda: self.host.enable_comp(laser1=False, polygon=False))()
                host_state = exe(self.host.get_state)()
                self.state["components"]["diodetest"] = host_state["photodiode_trigger"]
                if host_state == 0:
                    self.logger.error("Diode not triggered.")
                else:
                    self.logger.debug("Diode test passed.")

    async def print_loop(self, fname):
        self._stop.clear()
        self._pause.clear()
        self.reset_state()
        self.state["printing"] = True
        self.state["job"]["filename"] = fname
        self.state["job"]["laserpower"] = CONFIG["defaultprint"]["laserpower"]
        self.state["job"]["exposureperline"] = CONFIG["defaultprint"]["exposureperline"]
        if self._debug:
            self.logger.info(f"Printing with laserpower {self.state["job"]["laserpower"]}"
                             f" and {self.state["job"]["exposureperline"]} exposures.")
            # TODO: this would normally come from a file
            total_lines = 10
            
            self.state["job"]["totallines"] = total_lines
            start_time = time()
            for line in range(total_lines):
                self.logger.info(f"Exposing line {line}.")
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
        else:
            host = self.host
            bits_in_scanline = int(host.laser_params['BITSINSCANLINE'])
            words_in_line = wordsinscanline(bits_in_scanline)
            # we are going to replace the first command
            headers = {0: None, 1: None}
            for direction in [0,1]:
                line = host.bittobytelist(bitlst=[0]*bits_in_scanline, stepsperline=(1/self.state["job"]["exposureperline"]),
                                          direction=direction)
                cmdlst = host.bytetocmdlist(line)
                headers[direction] = cmdlst[0]

            with open(CONFIG["webserver"]["job_folder"] + f"/{fname}", "rb") as f:
                # 1. Header
                lanewidth = struct.unpack("<f", f.read(4))[0]
                facetsinlane = struct.unpack("<I", f.read(4))[0]
                lanes = struct.unpack("<I", f.read(4))[0]
                self.reset_state()
                self.state["job"]["totallines"] = int(facetsinlane * lanes)
                start_time = time()
                await asyncio.sleep(1) # time for propagation, update is pushed via SSE
                # z is not homed as it should be already in
                # position so laser is in focus
                host.enable_steppers = True
                laserpower = self.state["job"]["laserpower"]
                if (laserpower > 50) & (laserpower < 151):
                    self.host.laser_current = laserpower
                self.logger.info("Homing X- and Y-axis.")
                exe(lambda: host.home_axes([1, 1, 0]))()
                self.logger.info("Moving to start position.")
                # scanning direction offset is needed to prevent lock with home
                exe(lambda: host.gotopoint([70, 5, 0], absolute=False))()
                # enable scanhead
                exe(lambda: host.enable_comp(synchronize=True))()
                for lane in range(lanes):
                    # checks for communcation with frontend
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
                    self.state["job"]["currentline"] = int(lane * facetsinlane)
                    self.state["job"]["printingtime"] = round(time() - start_time)
                    await asyncio.sleep(1) # time for propagation, update pushed via SSE
                    # end checks communication front-end
                    self.logger.info(f"Exposing lane {lane+1} from {lanes}.")
                    if lane > 0:
                        self.logger.info("Moving in x-direction for next lane.")
                        exe(lambda: host.gotopoint(
                            [lanewidth, 0, 0], absolute=False
                        ))()
                    if lane % 2 == 1:
                        self.logger.info("Start exposing forward lane.")
                    else:
                        self.logger.info("Start exposing back lane.")

                    for _ in range(facetsinlane):
                        # Read the entire line's data into a buffer
                        line_data = f.read(words_in_line * 9)
                        for _ in range(self.state["job"]["exposureperline"]):
                            for word_index in range(words_in_line):
                                start_index = word_index * 9
                                cmddata = line_data[start_index : start_index + 9]
                                # the header is adapted
                                if word_index == 0:
                                    if lane % 2 == 1:
                                        cmddata = headers[0]
                                    else:
                                        cmddata = headers[1]
                                exe(lambda: host.send_command(cmddata, 
                                                              blocking=True))()
                    # send stopline
                    exe(lambda: host.writeline([]))()
            # disable scanhead
            await asyncio.sleep(1) # time for propagation
            self.logger.info("Waiting for stopline to execute.")
            exe(lambda: host.enable_comp(synchronize=False))()
            host.enable_steppers = False
            self.logger.info("Finished exposure.")
            self.state["printing"] = False




LASERHEAD = Laserhead()
