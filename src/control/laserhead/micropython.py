import logging
import asyncio
import struct
from time import time
from random import randint
import deflate

from hexastorm.fpga_host.micropython import ESP32Host
from hexastorm.config import Spi

from .base import BaseLaserhead
from ..constants import CONFIG


logger = logging.getLogger(__name__)


class Laserhead(BaseLaserhead, ESP32Host):
    def __init__(self):
        BaseLaserhead.__init__(self)
        ESP32Host.__init__(self)

    async def flash_fpga(self, filename):
        fname = CONFIG["fpga"]["storagefolder"] + f"/{filename}"
        await super().flash_fpga(fname)

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
        await BaseLaserhead.enable_comp(
            self,
            laser0=laser0,
            laser1=laser1,
            polygon=polygon,
            synchronize=synchronize,
            singlefacet=singlefacet,
        )
        await ESP32Host.enable_comp(
            self,
            laser0=laser0,
            laser1=laser1,
            polygon=polygon,
            synchronize=synchronize,
            singlefacet=singlefacet,
        )

    async def toggle_laser(self):
        await super().toggle_laser()
        await self.enable_comp(laser0=self.state["components"]["laser"])

    async def toggle_prism(self):
        await super().toggle_prism()
        await self.enable_comp(polygon=self.state["components"]["rotating"])

    async def move(self, vector):
        await super().move(vector)
        self.enable_steppers = True
        await super().gotopoint(vector, absolute=False)
        self.enable_steppers = False

    async def test_diode(self, timeout=3):
        logger.debug("Starting diode test.")
        self.state["components"]["diodetest"] = None
        await self.notify_listeners()
        await asyncio.sleep(timeout)
        self.state["components"]["diodetest"] = True if randint(0, 10) > 5 else False
        await self.notify_listeners()

    async def print_loop(self, fname):
        super().print_loop_prep(fname)
        exposures = self.state["job"]["exposureperline"]
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
                await self.notify_listeners()
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
                    if await self.handle_pausing_and_stopping():
                        break
                    self.state["job"]["currentline"] = int(lane * facets_lane)
                    self.state["job"]["printingtime"] = round(time() - start_time)
                    await self.notify_listeners()
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
                                if await self.handle_pausing_and_stopping():
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
                                await self.notify_listeners()
                                if await self.handle_pausing_and_stopping():
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
        await self.notify_listeners()
        logger.info("Waiting for stopline to execute.")
        await self.enable_comp(synchronize=False)
        self.enable_steppers = False
        if not await self.host.fpga_state["error"]:
            logger.info("Error detected during printing")
        logger.info(
            f"Finished exposure. Total printing time {self.state['job']['printingtime']}"
        )
        self.state["printing"] = False
        await self.notify_listeners()
