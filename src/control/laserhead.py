import logging
import asyncio
import struct
from time import time
from random import randint

from .constants import CONFIG 
from hexastorm.constants import wordsinscanline
from hexastorm.controller import Host
from hexastorm.controller import executor as exe

logger = logging.getLogger(__name__)


class Laserhead:
    def __init__(self, debug=False):
        self.host = Host(micropython=True)
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


    def laser_current(self, val):
        """sets maximum laser current of laser driver per channel
        """
        self.host.laser_current(val)

    @exe
    def write_line(self, bitlst, stepsperline=1, direction=0, repetitions=1):
        yield from self.host.writeline(bitlst, stepsperline, direction, repetitions)

    @exe
    def enable_comp(
        self, laser0=False, laser1=False, polygon=False, synchronize=False, singlefacet=False
    ):
        """enable components

        laser0   -- True enables laser channel 0
        laser1   -- True enables laser channel 1
        polygon  -- False enables polygon motor
        """
        logger.debug(f"laser0, laser1, polygon set to {laser0, laser1, polygon}")
        if not self._debug:
            yield from self.host.enable_comp(laser0=laser0, laser1=laser1, polygon=polygon, synchronize=synchronize, singlefacet=singlefacet)
            self.state["components"]["laser"]  = laser0 or laser1
            self.state["components"]["rotating"]  = polygon
            self.state["job"]["singlefacet"]  = singlefacet

    @exe
    def toggle_laser(self):
        laser = self.state["components"]["laser"]
        self.state["components"]["laser"] = laser = not laser
        logger.debug(f"Laser on is {laser}")
        if not self._debug:
            yield from self.host.enable_comp(laser0=laser)

    @exe
    def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        logger.debug(f"Change rotation state prism to {prism}.")
        if not self._debug:
            yield from self.host.enable_comp(polygon=prism)

    @exe
    def move(self, vector):
        logger.debug(f"Moving vector {vector}.")
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
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.NOTSET)
    
    async def test_diode(self, timeout=3):
        logger.debug("Starting diode test.")
        self.state["components"]["diodetest"] = None
        if self._debug:
            await asyncio.sleep(timeout)
            self.state["components"]["diodetest"] = (
                True if randint(0, 10) > 5 else False
            )
        else:
            host_state = exe(self.host.get_state)()
            if host_state["photodiode_trigger"] != 0:
                logger.error("Diode already triggered.")
                self.state["components"]["diodetest"] = False
            else:
                exe(lambda: self.host.enable_comp(laser1=True, polygon=True))()
                logger.debug(f"Wait for diode trigger, {timeout} seconds.")
                await asyncio.sleep(timeout)
                exe(lambda: self.host.enable_comp(laser1=False, polygon=False))()
                host_state = exe(self.host.get_state)()
                self.state["components"]["diodetest"] = host_state["photodiode_trigger"]
                if host_state == 0:
                    logger.error("Diode not triggered.")
                else:
                    logger.debug("Diode test passed. Stable test requires 15 seconds.")
                    self.enable_comp(synchronize=True)
                    await asyncio.sleep(15)
                    self.enable_comp(synchronize=False)

    async def print_loop(self, fname):
        async def handle_pausing_and_stopping():
            if self._pause.is_set():
                while self._pause.is_set() and not self._stop.is_set():
                    await asyncio.sleep(2)
                    logger.debug("Printloop in pause")
                logger.debug("Printloop resumed")
            if self._stop.is_set():
                return True
            return False


        self._stop.clear()
        self._pause.clear()
        self.reset_state()
        self.state["printing"] = True
        self.state["job"]["filename"] = fname
        self.state["job"]["laserpower"] = CONFIG["defaultprint"]["laserpower"]
        self.state["job"]["exposureperline"] = CONFIG["defaultprint"]["exposureperline"]
        self.state["job"]["singlefacet"] = CONFIG["defaultprint"]["singlefacet"]
        if self._debug:
            basestring = (f"Printing with laserpower {self.state["job"]["laserpower"]}"
                             f" and {self.state["job"]["exposureperline"]} exposures.")
            if self.state["job"]["singlefacet"]:
                basestring += "using a single facet."
            else:
                basestring += "without using a single facet."
            logger.info(basestring)
            # TODO: this would normally come from a file
            total_lines = 10
            
            self.state["job"]["totallines"] = total_lines
            start_time = time()
            for line in range(total_lines):
                logger.info(f"Exposing line {line}.")
                if await handle_pausing_and_stopping():
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
                self.state["job"]["totallines"] = int(facetsinlane * lanes)
                start_time = time()
                await asyncio.sleep(2) # time for propagation, update is pushed via SSE
                # z is not homed as it should be already in
                # position so laser is in focus
                host.enable_steppers = True
                laserpower = self.state["job"]["laserpower"]
                if (laserpower > 50) & (laserpower < 151):
                    self.host.laser_current = laserpower
                logger.info("Homing X- and Y-axis.")
                exe(lambda: host.home_axes([1, 1, 0]))()
                logger.info("Moving to start position.")
                # scanning direction offset is needed to prevent lock with home
                exe(lambda: host.gotopoint([70, 5, 0], absolute=False))()
                # enable scanhead
                exe(lambda: host.enable_comp(synchronize=True, singlefacet=self.state["job"]["singlefacet"]))()
                for lane in range(lanes):
                    if await handle_pausing_and_stopping():
                        break
                    self.state["job"]["currentline"] = int(lane * facetsinlane)
                    self.state["job"]["printingtime"] = round(time() - start_time)
                    await asyncio.sleep(1) # time for propagation, update pushed via SSE
                    # end checks communication front-end
                    logger.info(f"Exposing lane {lane+1} from {lanes}.")
                    if lane > 0:
                        logger.info("Moving in x-direction for next lane.")
                        exe(lambda: host.gotopoint(
                            [lanewidth, 0, 0], absolute=False
                        ))()
                    if lane % 2 == 1:
                        logger.info("Start exposing forward lane.")
                    else:
                        logger.info("Start exposing back lane.")
                    
                    aantalfacetten = int(host.laser_params['RPM']/self.state["job"]["exposureperline"])
                    if self.state["job"]["singlefacet"]:
                        aantalfacetten = int(aantalfacetten/4)
                    for facet in range(facetsinlane):
                        if facet % aantalfacetten == 0:
                            self.state["job"]["currentline"] = int(lane * facetsinlane) + facet
                            self.state["job"]["printingtime"] = round(time() - start_time)
                            await asyncio.sleep(1)
                            if await handle_pausing_and_stopping():
                                break
                        # Read the entire line's data into a buffer
                        line_data = f.read(words_in_line * 9)
                        def sendline():
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
                        for _ in range(self.state["job"]["exposureperline"]):
                            # NOTE: code clone a similar things is also available in controller
                            # known as retry on fpga error
                            max_attempts = 3
                            for attempt in range(max_attempts+1):
                                try:
                                    sendline()
                                    break # Exit loop if successfull
                                except Exception as e:
                                    # communication can fail, this is believed to originate from
                                    # the lack of CRC bytes.. This fix introduces a small error
                                    if "FPGA" in str(e):
                                        if attempt == max_attempts:
                                            logger.error("Communication with FPGA not succesfull, job aborted")
                                            host.reset()
                                            return
                                        else:
                                            logger.error("Error detected on FPGA, wait 3 seconds"
                                            "for buffer to deplete and try again.")
                                            await asyncio.sleep(3)
                                            host.reset()
                                            exe(lambda: host.enable_comp(synchronize=True, singlefacet=self.state["job"]["singlefacet"]))()
                                
                    # send stopline
                    exe(lambda: host.writeline([]))()
            # disable scanhead
            await asyncio.sleep(1) # time for propagation
            logger.info("Waiting for stopline to execute.")
            exe(lambda: host.enable_comp(synchronize=False))()
            host.enable_steppers = False
            logger.info(f"Finished exposure. Total printing time {self.state["job"]["printingtime"]}")
            self.state["printing"] = False




LASERHEAD = Laserhead()
