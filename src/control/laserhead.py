import logging
import asyncio
import struct
import deflate
from time import time
from random import randint

from .constants import CONFIG
from hexastorm.fpga_host.micropython import ESP32Host
from hexastorm.config import Spi

logger = logging.getLogger(__name__)


class Laserhead(ESP32Host):
    def __init__(self, debug=False):
        self.debug = debug
        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._start = asyncio.Event()
        self.reset_state()
        super().__init__()

    async def flash_fpga(self, filename):
        fname = CONFIG["fpga"]["storagefolder"] + f"/{filename}"
        await super().flash_fpga(fname)

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
            await super().enable_comp(
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
            await super().enable_comp(laser0=laser)

    async def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        logger.debug(f"Change rotation state prism to {prism}.")
        if not self._debug:
            await super().enable_comp(polygon=prism)

    async def move(self, vector):
        logger.debug(f"Moving vector {vector}.")
        if not self._debug:
            self.enable_steppers = True
            await super().gotopoint(vector, absolute=False)
            self.enable_steppers = False

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
                    logger.debug("Printing paused")
                logger.debug("Printing resumed")
            if self._stop.is_set():
                return True
            return False

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
            bits_scanline = int(self.cfg.laser_timing["scanline_length"])
            words_scanline = self.cfg.hdl_cfg.words_scanline
            bytes_command_word = Spi.command_bytes + Spi.word_bytes
            # protocol sends over command + word, where word can contain instruction
            # command needs to be adapted for exposures per line
            commands = {0: None, 1: None}
            for direction in [0, 1]:
                line = self.bit_to_byte_list(
                    laser_bits=[0] * bits_scanline,
                    steps_line=(1 / exposures),
                    direction=direction,
                )
                cmd_lst = self.byte_to_cmd_list(line)
                commands[direction] = cmd_lst[0]

            with open(CONFIG["webserver"]["job_folder"] + f"/{fname}", "rb") as f:
                with deflate.DeflateIO(f, deflate.ZLIB) as d:
                    # 1. Header
                    lane_width = struct.unpack("<f", d.read(4))[0]
                    facets_lane = struct.unpack("<I", d.read(4))[0]
                    lanes = struct.unpack("<I", d.read(4))[0]
                    self.state["job"]["totallines"] = int(facets_lane * lanes)
                    start_time = time()
                    await asyncio.sleep(2)
                    # time for propagation, update is pushed via SSE
                    # z is not homed as it should be already in
                    # position so laser is in focus
                    self.enable_steppers = True
                    laserpower = self.state["job"]["laserpower"]
                    if (laserpower > 50) & (laserpower < 151):
                        self.laser_current = laserpower
                    logger.info("Homing X- and Y-axis.")
                    await self.home_axes([1, 1, 0])
                    logger.info("Moving to start position.")
                    # scanning direction offset is needed to prevent lock with home
                    await self.gotopoint([70, 5, 0], absolute=False)
                    # enable scanhead
                    await self.enable_comp(
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
                            await self.gotopoint([lane_width, 0, 0], absolute=False)
                        if lane % 2 == 1:
                            logger.info("Start exposing forward lane.")
                        else:
                            logger.info("Start exposing back lane.")

                        total_facets = int(self.cfg.laser_timing["rpm"] / exposures)
                        if self.state["job"]["singlefacet"]:
                            total_facets = int(total_facets / 4)

                        if exposures == 1:
                            lines_chunk = self.cfg.hdl_cfg.lines_chunk
                            for facet in range(facets_lane, lines_chunk):
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
                                last_facet = min(facet + lines_chunk, lines_chunk)
                                to_read = last_facet - facet
                                line_data = d.read(
                                    words_scanline * bytes_command_word * to_read
                                )
                                await self.send_command(
                                    line_data,
                                    timeout=True,
                                )
                        else:
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
                                # change number of exposures in first word
                                line_data = bytearray(
                                    d.read(words_scanline * bytes_command_word)
                                )
                                if lane % 2 == 1:
                                    line_data[:bytes_command_word] = commands[0]
                                else:
                                    line_data[:bytes_command_word] = commands[1]
                                await self.send_command(
                                    list(line_data) * exposures,
                                    timeout=True,
                                )
                        # send stopline
                        await self.write_line([])
            # disable scanhead
            await asyncio.sleep(1)  # time for propagation
            logger.info("Waiting for stopline to execute.")
            await self.enable_comp(synchronize=False)
            self.enable_steppers = False
            if not await self.host.fpga_state["error"]:
                logger.info("Error detected during printing")
            logger.info(
                f"Finished exposure. Total printing time {self.state['job']['printingtime']}"
            )
            self.state["printing"] = False


LASERHEAD = Laserhead()
