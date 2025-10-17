import logging
import asyncio
import struct
import zlib
from time import time
from random import randint

from .constants import CONFIG
from hexastorm.fpga_host.micropython import ESP32Host

logger = logging.getLogger(__name__)


class Laserhead:
    def __init__(self, debug=False):
        # inherit from ESP32Host not possible due to asyncio loop issues
        self.host = ESP32Host(sync=False)
        self.debug = debug
        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._start = asyncio.Event()
        self.reset_state()

    async def flash_fpga(self, filename):
        fname = CONFIG["fpga"]["storagefolder"] + f"/{filename}"
        await self.host.flash_fpga(fname)

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
        if not self._debug:
            await self.host.enable_comp(
                laser0=laser0,
                laser1=laser1,
                polygon=polygon,
                synchronize=synchronize,
                singlefacet=singlefacet,
            )

    async def toggle_laser(self):
        laser = self.state["components"]["laser"]
        self.state["components"]["laser"] = laser = not laser
        logger.debug(f"Laser on is {laser}")
        if not self._debug:
            await self.host.enable_comp(laser0=laser)

    async def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        logger.debug(f"Change rotation state prism to {prism}.")
        if not self._debug:
            await self.host.enable_comp(polygon=prism)

    async def move(self, vector):
        logger.debug(f"Moving vector {vector}.")
        if not self._debug:
            self.host.enable_steppers = True
            await self.host.gotopoint(vector, absolute=False)
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
            fpga_state = await self.host.fpga_state
            if fpga_state["photodiode_trigger"] != 0:
                logger.error("Diode already triggered.")
                self.state["components"]["diodetest"] = False
            else:
                await self.host.enable_comp(laser1=True, polygon=True)
                logger.debug(f"Wait for diode trigger, {timeout} seconds.")
                await asyncio.sleep(timeout)
                await self.host.enable_comp(laser1=False, polygon=False)
                fpga_state = await self.host.fpga_state
                self.state["components"]["diodetest"] = fpga_state["photodiode_trigger"]
                if fpga_state == 0:
                    logger.error("Diode not triggered.")
                else:
                    logger.debug("Diode test passed. Stable test requires 15 seconds.")
                    await self.enable_comp(synchronize=True)
                    await asyncio.sleep(15)
                    await self.enable_comp(synchronize=False)

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

        host = self.host
        self._stop.clear()
        self._pause.clear()
        self.reset_state()
        self.state["printing"] = True
        self.state["job"]["filename"] = fname
        self.state["job"]["laserpower"] = CONFIG["defaultprint"]["laserpower"]
        self.state["job"]["exposureperline"] = CONFIG["defaultprint"]["exposureperline"]
        self.state["job"]["singlefacet"] = CONFIG["defaultprint"]["singlefacet"]
        basestring = (
            f"Printing with laserpower {self.state['job']['laserpower']}"
            f" and {self.state['job']['exposureperline']} exposures."
        )
        if self.state["job"]["singlefacet"]:
            basestring += "using a single facet."
        else:
            basestring += "without using a single facet."
        logger.info(basestring)
        if self._debug:
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
            bits_scanline = int(host.cfg.laser_timing["scanline_length"])
            words_scanline = host.cfg.hdl_cfg.words_scanline
            # protocol sends over command + word, where word can contain instruction
            # command needs to be adapted for exposures per line
            commands = {0: None, 1: None}
            for direction in [0, 1]:
                line = host.bit_to_byte_list(
                    laser_bits=[0] * bits_scanline,
                    steps_line=(1 / self.state["job"]["exposureperline"]),
                    direction=direction,
                )
                cmd_lst = host.byte_to_cmd_list(line)
                commands[direction] = cmd_lst[0]

            with open(CONFIG["webserver"]["job_folder"] + f"/{fname}", "rb") as f:
                f_decomp = zlib.DecompIO(f, wbits=15)
                # 1. Header
                lane_width = struct.unpack("<f", f_decomp.read(4))[0]
                facets_lane = struct.unpack("<I", f_decomp.read(4))[0]
                lanes = struct.unpack("<I", f_decomp.read(4))[0]
                self.state["job"]["totallines"] = int(facets_lane * lanes)
                start_time = time()
                await asyncio.sleep(2)
                # time for propagation, update is pushed via SSE
                # z is not homed as it should be already in
                # position so laser is in focus
                host.enable_steppers = True
                laserpower = self.state["job"]["laserpower"]
                if (laserpower > 50) & (laserpower < 151):
                    host.laser_current = laserpower
                logger.info("Homing X- and Y-axis.")
                await host.home_axes([1, 1, 0])
                logger.info("Moving to start position.")
                # scanning direction offset is needed to prevent lock with home
                await host.gotopoint([70, 5, 0], absolute=False)
                # enable scanhead
                await host.enable_comp(
                    synchronize=True,
                    singlefacet=self.state["job"]["singlefacet"],
                )
                for lane in range(lanes):
                    if await handle_pausing_and_stopping():
                        break
                    self.state["job"]["currentline"] = int(lane * facets_lane)
                    self.state["job"]["printingtime"] = round(time() - start_time)
                    await asyncio.sleep(1)
                    # time for propagation, update pushed via SSE
                    # end checks communication front-end
                    logger.info(f"Exposing lane {lane + 1} from {lanes}.")
                    if lane > 0:
                        logger.info("Moving in x-direction for next lane.")
                        await host.gotopoint([lane_width, 0, 0], absolute=False)
                    if lane % 2 == 1:
                        logger.info("Start exposing forward lane.")
                    else:
                        logger.info("Start exposing back lane.")

                    total_facets = int(
                        host.cfg.laser_timing["rpm"]
                        / self.state["job"]["exposureperline"]
                    )
                    if self.state["job"]["singlefacet"]:
                        total_facets = int(total_facets / 4)
                    for facet in range(facets_lane):
                        if facet % total_facets == 0:
                            self.state["job"]["currentline"] = (
                                int(lane * facets_lane) + facet
                            )
                            self.state["job"]["printingtime"] = round(
                                time() - start_time
                            )
                            await asyncio.sleep(1)
                            if await handle_pausing_and_stopping():
                                break
                        # Read the entire line's data into a buffer
                        line_data = f_decomp.read(words_scanline * 9)
                        for _ in range(self.state["job"]["exposureperline"]):
                            # sendline
                            for word_index in range(words_scanline):
                                start_index = word_index * 9
                                cmd_data = line_data[start_index : start_index + 9]
                                # the header is adapted
                                if word_index == 0:
                                    if lane % 2 == 1:
                                        cmd_data = commands[0]
                                    else:
                                        cmd_data = commands[1]
                                await host.send_command(cmd_data, blocking=True)
                    # send stopline
                    await host.write_line([])
            # disable scanhead
            await asyncio.sleep(1)  # time for propagation
            logger.info("Waiting for stopline to execute.")
            await host.enable_comp(synchronize=False)
            host.enable_steppers = False
            logger.info(
                f"Finished exposure. Total printing time {self.state['job']['printingtime']}"
            )
            self.state["printing"] = False


LASERHEAD = Laserhead()
