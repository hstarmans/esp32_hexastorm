import logging
import asyncio
import struct
from time import time
import deflate

from hexastorm.fpga_host.micropython import ESP32Host
from hexastorm.fpga_host.syncwrap import syncable
from hexastorm.fpga_host.tools import find_shift
from hexastorm.config import Spi

try:
    import numpy as np
except ImportError:
    from ulab import numpy as np

from .base import BaseLaserhead
from .. import constants

logger = logging.getLogger(__name__)


class Laserhead(BaseLaserhead, ESP32Host):
    def __init__(self):
        ESP32Host.__init__(self)
        BaseLaserhead.__init__(
            self
        )  # overwrites self._position, motors need to be present
        self.cur_facet_means = None

    @property
    def facet_means(self):
        "Retrieve period per facet in ms as list"
        return constants.CONFIG["laserhead"]["facetmeans"]

    async def update_facet_means(self):
        "Set period per facet in ms with list"
        self.cur_facet_means = await self.measure_facet_means()
        constants.CONFIG["laserhead"]["facetmeans"] = self.cur_facet_means
        constants.update_config()

    async def remap(self, facet_id=0):
        """
        Maps a calibrated facet ID to its current physical index
        based on rotational shift.

        facet_id  -- facet_id to determine internal facet off
        """
        # Calculate how many positions the facets have rotated
        shift = find_shift(self.cur_facet_means, self.facet_means)[0]

        # Apply the shift to find the new position
        num_facets = self.cfg.laser_timing["facets"]
        current_index = (facet_id + shift) % num_facets

        return current_index

    def apply_motor_settings(self):
        """Explicitly push config.json settings directly to the TMC drivers."""
        motors_config = constants.CONFIG["motors"]

        # Extract metadata from the block
        non_tmc_keys = set(motors_config["non_tmc_keys"])
        motor_globals = motors_config["motor_globals"]

        # set values on TMC2209
        for ax_name, tmc in self.steppers.items():
            # 1. Apply global fallbacks first
            for key, value in motor_globals.items():
                if key not in non_tmc_keys:
                    setattr(tmc, key, value)

            # 2. Apply axis-specific overrides (ensure it's actually an axis dictionary)
            if ax_name in motors_config and isinstance(motors_config[ax_name], dict):
                for key, value in motors_config[ax_name].items():
                    if key not in non_tmc_keys:
                        setattr(tmc, key, value)

        super().apply_motor_settings()

    async def enable_comp(self, **kwargs):
        """enable components

        laser0   -- True enables laser channel 0
        laser1   -- True enables laser channel 1
        polygon  -- False enables polygon motor
        """
        await BaseLaserhead.enable_comp(self, **kwargs)
        await ESP32Host.enable_comp(self, **kwargs)

    async def toggle_laser(self):
        await super().toggle_laser()
        await self.enable_comp(laser0=self.state["components"]["laser"])

    async def toggle_prism(self):
        await super().toggle_prism()
        await self.enable_comp(polygon=self.state["components"]["rotating"])

    async def gotopoint(
        self,
        position,
        speed=None,
        absolute=True,
        check_sensors=True,
        workspace=False,
    ):
        """
        Wraps the hardware gotopoint function to automatically handle stepper enabling
        and state notifications for the web UI.
        """
        # 1. Execute hardware command (blocks until physical move is complete)
        result = await ESP32Host.gotopoint(
            self,
            position=position,
            speed=speed,
            absolute=absolute,
            workspace=workspace,
            check_sensors=check_sensors,
        )

        # 2. Update RAM and NVS instantly using the synchronous helper from base.py
        self._save_position()

        # 3. Trigger SSE update for the web clients
        await self.notify_listeners()
        return result

    async def home_axes(self, axes):
        logger.info(f"Homing axes {axes}.")
        await ESP32Host.home_axes(self, axes)

        self._save_position()
        await self.notify_listeners()

    async def set_workspace_zero(self, axes=None):
        # 1. Allow the FPGA/hardware layer to reset its internal offsets
        ESP32Host.set_workspace_zero(self, axes)

        # 2. Update the python state tracking and save to NVS
        self._update_workspace_zero(axes)
        await self.notify_listeners()

    async def set_spindle(self, value: int):
        await BaseLaserhead.set_spindle(self, value)
        await self.set_spindle_speed(self.state["components"]["spindle"])
        await self.notify_listeners()

    async def set_fan(self, value: int):
        await BaseLaserhead.set_fan(self, value)
        await self.set_fan_speed(self.state["components"]["fan"])
        await self.notify_listeners()

    async def synchronize(self, value=True):
        """Synchronize laser with phodiode.

        Args:
            value (bool): True to enable synchronization, False to disable.
        """
        await super().synchronize(value)
        if value:
            self.cur_facet_means = await self.measure_facet_means()
        else:
            self.cur_facet_means = None

    async def test_diode(self):
        logger.debug("Starting diode test.")
        self.state["components"]["diodetest"] = None
        await self.notify_listeners()
        # simulate time needed for measurement
        await asyncio.sleep(3)
        cur_sync = (await self.fpga_state)["synchronized"]
        if not cur_sync:
            await self.synchronize(True)

        shift = find_shift(self.cur_facet_means, self.facet_means)[0]

        # 2. Pass the shift down to the parent for accurate logging
        self.state["components"]["diodetest"] = await super().test_laserhead(
            shift=shift
        )
        await self.notify_listeners()
        if not cur_sync:
            await self.synchronize(False)

    async def execute_gcode(self, fname):
        """
        Parses and executes a standard G-code file for 2.5D PCB milling.
        Supports:
            G0/G1: Linear motion
            G90/G91: Absolute/Relative positioning
            G21: Millimeters mode (enforced)
            F: Feedrate (mm/min -> converted to mm/s)
            M3/M5: Spindle On/Off
        """
        logger.info(f"Starting G-Code execution: {fname}")

        # Setup default state for execution
        # Spindle starts off, positioning starts absolute
        is_absolute = True
        current_feedrate_mms = 10.0  # Default fallback speed
        self.state["printing"] = True
        self.enable_steppers = True
        await self.notify_listeners()

        # We need a tracker for current position because G-code can omit axes.
        # e.g., if we are at [10, 10, 0] and command is "G1 X20", Y and Z remain unchanged.
        # We initialize it with our current WPOS (Workspace Position)
        gcode_pos = self.wpos

        try:
            filepath = self.get_job_path(fname)
            with open(filepath, "r") as f:
                for line_num, line in enumerate(f):
                    # Check for pause/stop from the web UI
                    if await self.handle_pausing_and_stopping():
                        logger.info("G-code execution aborted/stopped by user.")
                        break

                    # Parse the line
                    # Strip comments starting with ';' or '('
                    line = line.split(";")[0].split("(")[0].strip().upper()
                    if not line:
                        continue

                    tokens = line.split()
                    cmd = tokens[0]

                    # Parse parameters into a dictionary (e.g. {'X': 10.5, 'F': 300})
                    params = {}
                    for token in tokens[1:]:
                        if len(token) > 1 and token[0] in "XYZFSIJ":
                            try:
                                params[token[0]] = float(token[1:])
                            except ValueError:
                                pass  # Ignore malformed tokens

                    # Execution State Machine
                    if cmd == "G21":
                        pass  # Millimeters - expected default

                    elif cmd == "G20":
                        logger.warning("G20 (Inches) is not supported. Halting.")
                        break

                    elif cmd == "G90":
                        is_absolute = True

                    elif cmd == "G91":
                        is_absolute = False

                    elif cmd in ["M3", "M03"]:
                        # Spindle On (Default 255 if S is not provided)
                        spindle_speed = int(params.get("S", 255))
                        await self.set_spindle(spindle_speed)

                    elif cmd in ["M5", "M05"]:
                        # Spindle Off
                        await self.set_spindle(0)

                    elif cmd in ["G0", "G00", "G1", "G01"]:
                        # Linear Motion
                        # Update feedrate if provided (G-code feedrate is mm/min, gotopoint needs mm/s)
                        if "F" in params:
                            current_feedrate_mms = params["F"] / 60.0

                        # Determine speed (Rapid vs Feed)
                        # Assume rapid G0 is just a fast feedrate (e.g., 20 mm/s).
                        # Adjust this based on your machine's physical limits.
                        move_speed = (
                            20.0 if cmd in ["G0", "G00"] else current_feedrate_mms
                        )

                        # Construct target position
                        # If an axis is missing in the command, it stays at its current value
                        target_pos = list(gcode_pos)  # Copy current state

                        if is_absolute:
                            if "X" in params:
                                target_pos[0] = params["X"]
                            if "Y" in params:
                                target_pos[1] = params["Y"]
                            if "Z" in params:
                                target_pos[2] = params["Z"]
                            # Update our internal tracker
                            gcode_pos = list(target_pos)
                        else:
                            dx = params.get("X", 0.0)
                            dy = params.get("Y", 0.0)
                            dz = params.get("Z", 0.0)
                            target_pos = [dx, dy, dz]
                            # Update our internal tracker for future lines
                            gcode_pos[0] += dx
                            gcode_pos[1] += dy
                            gcode_pos[2] += dz

                        # Execute the physical move
                        # G-code targets are inherently workspace coordinates (relative to origin)
                        await self.gotopoint(
                            position=target_pos,
                            speed=move_speed,
                            absolute=is_absolute,
                            workspace=True,  # G-code ALWAYS operates in workspace coords
                            check_sensors=False,  # Head experiences force by definition
                        )

        except OSError:
            logger.error(f"G-code file not found: {fname}")
        except Exception as e:
            logger.error(f"Error executing G-code at line {line_num}: {e}")
        finally:
            # Clean up
            await self.set_spindle(0)
            await self.wait_fifo_empty()
            self.enable_steppers = False
            self.state["printing"] = False
            await self.notify_listeners()
            logger.info("G-code execution finished.")

    async def print_loop(self, fname):
        await self.flush_buffer()  # Light weight reset: Flushes FIFO & resets FPGA fsm state
        await super().print_loop_prep(fname)
        await self.notify_listeners()
        await asyncio.sleep(0)
        exposures = self.state["job"]["exposureperline"]
        bits_scanline = int(self.cfg.laser_timing["scanline_length"])
        words_scanline = self.cfg.hdl_cfg.words_scanline
        bytes_command_word = Spi.command_bytes + Spi.word_bytes
        # a laserline instruction is: command + word
        # we read the stored instruction from memory but want to change
        # the command, e.g. steps size after each line
        commands = {0: None, 1: None}
        for direction in [0, 1]:
            line = self.bit_to_byte_list(
                laser_bits=[0] * bits_scanline,
                steps_line=(1 / exposures),
                direction=direction,
            )
            cmd_lst = self.byte_to_cmd_list(line)
            commands[direction] = cmd_lst[0]

        with open(self.get_job_path(fname), "rb") as f, deflate.DeflateIO(
            f, deflate.ZLIB
        ) as d:
            # Header
            correction = constants.CONFIG["defaultprint"]["lanewidth_correction"]
            logger.info(f"Lanewdith correction {correction}.")
            lane_width = struct.unpack("<f", d.read(4))[0] + correction
            facets_lane = struct.unpack("<I", d.read(4))[0]
            lanes = struct.unpack("<I", d.read(4))[0]
            self.state["job"]["totallines"] = int(facets_lane * lanes)
            start_time = time()
            await self.notify_listeners()
            # z is not homed as it should be already in
            # position so laser is in focus
            self.enable_steppers = True
            laserpower = self.state["job"]["laserpower"]
            self.laser_current = laserpower

            # homing logic

            cfg_print = constants.CONFIG["defaultprint"]
            # Homing logic
            if cfg_print["home_before_print"]:
                logger.info("Homing X- and Y-axis.")
                await self.home_axes([1, 1, 0])
            else:
                logger.info("Skipping homing before print per operator settings.")
            # Deciding start position
            custom_origin = cfg_print["workspace_origin"]
            if cfg_print["use_custom_start"] and custom_origin is not None:
                logger.info(
                    f"Overriding workspace origin to custom MPOS: {custom_origin}"
                )
                self._work_offset = np.array(custom_origin, dtype=float)
                self._save_position()

            logger.info("Moving to workspace origin (WPOS 0, 0, 0).")
            await self.gotopoint([0.0, 0.0, 0.0], absolute=True, workspace=True)

            # enable scanhead
            await self.enable_comp(
                synchronize=True,
                singlefacet=self.state["job"]["singlefacet"],
            )
            await asyncio.sleep(2)  # wait for stabilization
            # ensure facet 0 is at the start
            offset_0 = await self.remap(facet_id=0)
            # internal facet counter needs to align with calibration table
            if offset_0 != 0:
                logger.info(
                    f"Rotational offset detected: shifting start by {offset_0} lines."
                )
                self.enable_steppers = False
                dummy_line = [0] * bits_scanline
                for _ in range(offset_0):
                    await self.write_line(dummy_line)
                self.enable_steppers = True
            for lane in range(lanes):
                if await self.handle_pausing_and_stopping():
                    break
                self.state["job"]["currentline"] = int(lane * facets_lane)
                self.state["job"]["printingtime"] = round(time() - start_time)
                await self.notify_listeners()
                logger.info(f"Exposing lane {lane + 1} from {lanes}.")
                if lane > 0:
                    logger.info("Moving in y-direction for next lane.")
                    await self.gotopoint([0, lane_width, 0], absolute=False)
                if lane % 2 == 1:
                    logger.info("Start exposing forward lane.")
                else:
                    logger.info("Start exposing back lane.")

                total_facets = int(self.cfg.laser_timing["rpm"] / exposures)
                if self.state["job"]["singlefacet"]:
                    total_facets = int(total_facets / 4)

                if exposures == 1:
                    lines_chunk = self.cfg.hdl_cfg.lines_chunk
                    for facet in range(0, facets_lane, lines_chunk):
                        if facet % 1000 == 0:
                            self.state["job"]["currentline"] = (
                                int(lane * facets_lane) + facet
                            )
                            self.state["job"]["printingtime"] = round(
                                time() - start_time
                            )
                            await self.notify_listeners()
                            if await self.handle_pausing_and_stopping():
                                break
                        last_facet = min(facet + lines_chunk, facets_lane)
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
        await self.wait_fifo_empty()
        self.enable_steppers = False
        if (await self.fpga_state)["error"]:
            logger.info("Error detected during printing")
        logger.info(
            f"Finished exposure. Total printing time {self.state['job']['printingtime']}"
        )
        self.state["printing"] = False
        await self.notify_listeners()


@syncable
class LaserheadSync(Laserhead):
    def __init__(self, sync=True):
        super().__init__()
