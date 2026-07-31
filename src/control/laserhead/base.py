import asyncio
import logging
import struct
from random import randint
from time import time

import deflate

try:
    import numpy as np

    NP_FLOAT = float
except ImportError:
    from ulab import numpy as np

    NP_FLOAT = np.float

from hexastorm.config import PlatformConfig, Spi

from .. import constants
from ..constants import CONFIG, NVS_STORE

logger = logging.getLogger(__name__)


def _parse_job_suffix(fname):
    """Extract per-line exposure count and optical-correction flag from the job filename.

    Expected filename stem pattern: ``<base>_e<N>_<cor|nocor>``
    e.g. ``circuit_e1_nocor.pat``  or  ``circuit_e4_cor.pat``

    Returns:
        (file_exposures: int, optical_correction: bool), or (None, None) if the
        suffix is absent or malformed.
    """
    # Strip path and extension, work with the bare stem
    stem = fname.rsplit(".", 1)[0].rsplit("/", 1)[-1]
    parts = stem.split("_")
    if len(parts) < 3:  # need at least <base> _e<N> _<cor|nocor>
        return None, None
    cor_part = parts[-1]
    exp_part = parts[-2]
    if cor_part not in ("cor", "nocor"):
        return None, None
    if not exp_part.startswith("e"):
        return None, None
    try:
        file_exposures = int(float(exp_part[1:]))
    except ValueError:
        return None, None
    return file_exposures, cor_part == "cor"


class BaseLaserhead:
    def __init__(self):
        self.cfg = PlatformConfig(test=False)  # overwritten by derived classes

        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._start = asyncio.Event()
        self.statechange = asyncio.Event()
        self._debug = False
        self._laser_current = 0
        self._enable_steppers = False

        # Load coodinates from NVS flash database

        self._position = np.array(
            [
                NVS_STORE.get_int("mpos_x", 0) / 1000.0,
                NVS_STORE.get_int("mpos_y", 0) / 1000.0,
                NVS_STORE.get_int("mpos_z", 0) / 1000.0,
            ],
            dtype=NP_FLOAT,
        )

        self._work_offset = np.array(
            [
                NVS_STORE.get_int("woff_x", 0) / 1000.0,
                NVS_STORE.get_int("woff_y", 0) / 1000.0,
                NVS_STORE.get_int("woff_z", 0) / 1000.0,
            ],
            dtype=NP_FLOAT,
        )

        self.apply_motor_settings()
        self.reset_state()

    @property
    def laser_current(self):
        return getattr(self, "_laser_current", 0)

    @laser_current.setter
    def laser_current(self, val):
        self._laser_current = val
        logger.debug(f"Mock laser_current set to {val}")

    @property
    def enable_steppers(self):
        return getattr(self, "_enable_steppers", False)

    @enable_steppers.setter
    def enable_steppers(self, val):
        self._enable_steppers = bool(val)
        logger.debug(f"Mock enable_steppers set to {val}")

    def get_job_path(self, fname):
        """Returns the full path for a job file in the webserver job folder."""
        return f"{CONFIG['webserver']['job_folder']}/{fname}"

    def _save_position(self):
        """Saves the current machine coordinates and work offsets directly to NVS."""
        NVS_STORE.save_state(self.mpos, self._work_offset)

    def reset_state(self):
        job = {
            "currentline": 0,
            "totallines": 0,
            "printingtime": 0,
            "exposureperline": 1,
            "singlefacet": False,
            "laserpower": 130,
            "filename": "no file name",
            "workspace_origin": [0.0, 0.0, 0.0],
        }
        job.update(CONFIG["defaultprint"])
        components = {
            "laser": False,
            "diodetest": None,
            "rotating": False,
            "spindle": 0,  # spindle pwm [0-255]
            "fan": 0,  # fan pwm [0-255]
        }
        state = {
            "printing": False,
            "paused": False,
            "error_message": None,
            "job": job,
            "components": components,
            "mpos": self.mpos,
            "wpos": self.wpos,
        }
        self._state = state

    async def set_error(self, msg: str | None):
        self.state["error_message"] = msg
        self.state["printing"] = False
        logger.error(msg)
        await self.notify_listeners()

    async def clear_error(self):
        self.state["error_message"] = None
        await self.notify_listeners()

    @property
    def mpos(self):
        """Get machine position. Supports both numpy arrays (hardware) and lists (mock)."""
        return self._position.tolist()

    @property
    def wpos(self):
        """Get workspace position (mpos - work_offset) for both numpy and list types."""
        return (self._position - self._work_offset).tolist()

    # --- SYNCHRONOUS COORDINATE HELPERS ---
    # These execute instant vector math and save to NVS, without blocking the hardware loop.

    def _update_coordinates(self, position, absolute=True, workspace=False):
        """Instant math execution for coordinate updates."""
        pos_array = np.array(position, dtype=NP_FLOAT)

        if absolute:
            if workspace:
                # WPOS to MPOS conversion: MPOS = WPOS + Offset
                self._position = pos_array + self._work_offset
            else:
                self._position = pos_array.copy()
        else:
            # Relative movement (Jogging)
            self._position += pos_array

        self._save_position()

    def _update_home_coordinates(self, axes):
        """Mock behavior: Resets machine coordinates to the offset_mm (pull-off distance)."""
        axis_names = ["x", "y", "z"]

        # Read the live offsets from our math dictionary
        offsets = [self.cfg.motor_cfg["offset_mm"].get(ax, 0.0) for ax in axis_names]

        for i in range(len(axes)):
            if axes[i] == 1:
                # The mock machine rests at the pull-off offset, exactly like the real one!
                self._position[i] = offsets[i]

        self._save_position()

    def _update_workspace_zero(self, axes=None):
        """Calculates and applies the new workspace offset based on current machine position."""
        if axes is None:
            axes = [1, 1, 1]

        for i in range(len(axes)):
            if axes[i] == 1:
                # To make WPOS 0, the offset must equal the current MPOS
                self._work_offset[i] = self._position[i]
        self._save_position()

    def _validate_target_position(self, position, absolute=True, workspace=False):
        """Validate target position against axis min/max soft limits."""
        pos_array = np.array(position, dtype=NP_FLOAT)
        if absolute:
            target_mpos = (pos_array + self._work_offset) if workspace else pos_array.copy()
        else:
            target_mpos = self._position + pos_array

        axis_names = ["x", "y", "z"]
        motors_cfg = CONFIG.get("motors", {})
        for idx, ax in enumerate(axis_names):
            if idx >= len(target_mpos):
                break
            ax_cfg = motors_cfg.get(ax, {})
            min_limit = ax_cfg.get("min_mm", None)
            max_limit = ax_cfg.get("max_mm", None)
            val = float(target_mpos[idx])
            if min_limit is not None and val < float(min_limit):
                raise ValueError(f"Target {ax.upper()} position ({val:.2f} mm) is below min limit ({min_limit:.2f} mm)")
            if max_limit is not None and val > float(max_limit):
                raise ValueError(f"Target {ax.upper()} position ({val:.2f} mm) exceeds max limit ({max_limit:.2f} mm)")

    # --- MOCK / PC ASYNC METHODS ---
    # These include simulated delays and call the synchronous helpers above.

    async def gotopoint(
        self,
        position,
        speed=None,
        absolute=True,
        workspace=False,
        check_sensors=False,
    ):
        """Simulates target movement and updates mock coordinates over time."""
        self._validate_target_position(position, absolute=absolute, workspace=workspace)
        logger.info(f"Mock moving to {position} (abs={absolute}, wpos={workspace}).")

        # Simulate physical transit time (Great for UI testing!)
        await asyncio.sleep(0.3)

        self._update_coordinates(position, absolute, workspace)
        await self.notify_listeners()

    async def home_axes(self, axes):
        """Mock homing: simulates travel and rests at offset_mm."""
        axis_names = ["x", "y", "z"]
        homing_dirs = [
            self.cfg.motor_cfg["homing_dir"].get(ax, -1) for ax in axis_names
        ]
        offsets = [self.cfg.motor_cfg["offset_mm"].get(ax, 0.0) for ax in axis_names]

        logger.info(
            f"Mock homing axes {axes}. Directions: {homing_dirs}, Pull-off: {offsets}"
        )

        await asyncio.sleep(0.8)  # Simulate homing travel time

        self._update_home_coordinates(axes)
        await self.notify_listeners()

    async def set_workspace_zero(self, axes=None):
        """Mock workspace zero."""
        logger.info(f"Mock setting workspace zero for axes {axes}.")
        self._update_workspace_zero(axes)
        await self.notify_listeners()

    # --- SYSTEM CONTROL METHODS ---

    async def emergency_stop(self):
        """Emergency stop: signals cancellation, disables steppers, turns off laser."""
        logger.warning("EMERGENCY ABORT triggered!")
        self._stop.set()
        self.state["components"]["laser"] = False
        self.state["components"]["spindle"] = 0
        self.state["components"]["fan"] = 0
        self.state["printing"] = False
        self.state["paused"] = False
        self.enable_steppers = False
        await self.notify_listeners()

    async def stop_print(self):
        logger.info("Print is stopped.")
        self._stop.set()
        await self.notify_listeners()

    async def pause_print(self):
        logger.info("Print is paused.")
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()
        self.state["paused"] = self._pause.is_set()
        await self.notify_listeners()

    async def toggle_laser(self):
        laser = self.state["components"]["laser"]
        self.state["components"]["laser"] = laser = not laser
        logger.info(f"Laser on is {laser}")

    async def toggle_prism(self):
        prism = self.state["components"]["rotating"]
        self.state["components"]["rotating"] = prism = not prism
        logger.info(f"Change rotation state prism to {prism}.")

    async def set_spindle(self, value: int):
        value = max(0, min(255, int(value)))
        self.state["components"]["spindle"] = value
        logger.info(f"Spindle PWM set to {value}")

    async def set_fan(self, value: int):
        value = max(0, min(255, int(value)))
        self.state["components"]["fan"] = value
        logger.info(f"Fan PWM set to {value}")

    def apply_motor_settings(self):
        motors_config = CONFIG["motors"]
        non_tmc_keys = set(motors_config["non_tmc_keys"])

        # update hexastorm side
        for ax_name, settings in motors_config.items():
            if isinstance(settings, dict) and ax_name not in ["motor_globals"]:
                for key in non_tmc_keys:
                    if key in settings:
                        # Ensures the FPGA interpolator uses the UI's steps_mm and limits
                        self.cfg.motor_cfg[key][ax_name] = settings[key]

        logger.info("Motor settings pushed to TMC and hexastorm layer.")

    @property
    def state(self):
        """Dynamically populates the current coordinates into the state dictionary."""
        self._state["mpos"] = self.mpos
        self._state["wpos"] = self.wpos
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
        return bool(self._stop.is_set())

    # -------------------------------------------------------------------------
    # HARDWARE & FPGA STUBS (Mock for CPython; overridden by ESP32Host on HW)
    # -------------------------------------------------------------------------

    async def synchronize(self, value=True):
        logger.debug(f"Mock synchronize: {value}")
        return True

    async def remap(self, facet_id=0):
        logger.debug(f"Mock remap: facet_id {facet_id} -> 0")
        return 0

    async def send_command(self, command, timeout=0):
        if hasattr(command, "__len__") and len(command) > 0:
            words_scanline = getattr(self.cfg.hdl_cfg, "words_scanline", 1)
            bytes_command_word = Spi.command_bytes + Spi.word_bytes
            line_bytes = words_scanline * bytes_command_word
            n_lines = len(command) // line_bytes
            if n_lines > 0:
                rpm = self.cfg.laser_timing["rpm"]
                facets = self.cfg.laser_timing["facets"]
                line_time = 60.0 / (
                    rpm * facets * 5
                )  # 5 is used to speed up sending the commands.
                duration = n_lines * line_time
                now = time()
                target_time = getattr(self, "_mock_target_time", 0)
                target_time = max(target_time, now)
                target_time += duration
                self._mock_target_time = target_time

                ahead = target_time - now
                if ahead > 0.02:
                    await asyncio.sleep(min(0.1, ahead))
        return bytearray(len(command) if hasattr(command, "__len__") else 0)

    async def _read_fpga_state(self, data=None):
        return {
            "parsing": False,
            "error": False,
            "mem_full": False,
            "mem_empty": True,
            "photodiode_trigger": False,
            "synchronized": True,
        }

    @property
    def fpga_state(self):
        return self._read_fpga_state()

    async def set_parsing(self, enabled):
        logger.debug(f"Mock set_parsing: {enabled}")

    async def write_line(
        self, bit_lst, steps_line=1, direction=0, repetitions=1, facet=None
    ):
        rpm = self.cfg.laser_timing["rpm"]
        facets = self.cfg.laser_timing["facets"]
        line_time = (60.0 / (rpm * facets)) * repetitions
        await asyncio.sleep(line_time)
        logger.debug(f"Mock write_line called (slept {line_time:.6f}s)")

    async def wait_fifo_empty(
        self, poll_interval=0.01, check_sensors=False, timeout=5.0
    ):
        await asyncio.sleep(0)
        logger.debug("Mock wait_fifo_empty called")

    async def enable_comp(
        self,
        laser0=None,
        laser1=None,
        polygon=None,
        synchronize=None,
        singlefacet=None,
    ):
        """enable components"""
        logger.debug(f"laser0, laser1, polygon set to {laser0, laser1, polygon}")
        if laser0 is not None or laser1 is not None:
            self.state["components"]["laser"] = bool(laser0) or bool(laser1)
        if polygon is not None:
            self.state["components"]["rotating"] = polygon
        if singlefacet is not None:
            self.state["job"]["singlefacet"] = singlefacet

    async def flush_buffer(self):
        logger.info("Flushing buffer")

    def bit_to_byte_list(self, laser_bits, steps_line, direction):
        return [0] * 30

    def byte_to_cmd_list(self, byte_list):
        cmd_len = Spi.command_bytes + Spi.word_bytes
        return [b"\x00" * cmd_len] * 30

    async def print_loop_prep(self, fname):
        self._stop.clear()
        self._pause.clear()
        self._mock_target_time = 0
        self.reset_state()
        self.state["printing"] = True
        self.state["job"]["filename"] = fname
        self.state["job"]["laserpower"] = CONFIG["defaultprint"]["laserpower"]
        file_exposures, optical_correction = _parse_job_suffix(fname)
        if file_exposures is not None:
            exposures = self.state["job"]["exposureperline"] = file_exposures
        exposures_gui = CONFIG["defaultprint"]["exposureperline"]
        single_facet = self.state["job"]["singlefacet"] = CONFIG["defaultprint"][
            "singlefacet"
        ]
        if exposures == 1 and optical_correction is False:
            exposures = self.state["job"]["exposureperline"] = exposures_gui
        elif exposures_gui != 1:
            logger.debug(
                f"File has optical correction or optical correction and does not support {exposures_gui} exposures"
            )
            return False
        elif single_facet and optical_correction:
            logger.debug(
                "Single facet engraving cannot be combined with optical correction."
            )
            return False

        basestring = (
            f"Printing with laserpower {self.state['job']['laserpower']}"
            f" and {exposures} exposures, "
        )
        if self.state["job"]["singlefacet"]:
            basestring += "using a single facet."
        else:
            basestring += "without using a single facet."
        logger.info(basestring)
        return True

    async def print_loop(self, fname):
        # Light weight reset: Flushes FIFO & resets FPGA fsm state
        await self.flush_buffer()
        success = await self.print_loop_prep(fname)
        if not success:
            return
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

        with open(self.get_job_path(fname), "rb") as f:  # noqa: SIM117, ASYNC230, in micropython this should be done
            with deflate.DeflateIO(f, deflate.ZLIB) as d:
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
                try:
                    self.laser_current = laserpower
                except OSError as e:
                    logger.error(f"Aborting print: {e}")
                    await self.enable_comp(synchronize=False)
                    self.enable_steppers = False
                    self.state["printing"] = False
                    await self.notify_listeners()
                    return

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
                    self._work_offset = np.array(custom_origin, dtype=NP_FLOAT)
                    self._save_position()

                logger.info("Moving to workspace origin (WPOS 0, 0).")
                current_wpos_z = float(self.wpos[2])
                await self.gotopoint(
                    [0.0, 0.0, current_wpos_z],
                    absolute=True,
                    workspace=True,
                    check_sensors=False,
                )

                # Ensure FPGA parsing is enabled so component commands take effect
                await self.set_parsing(True)

                # enable scanhead components including polygon motor
                await self.enable_comp(
                    singlefacet=self.state["job"]["singlefacet"],
                )
                await asyncio.sleep(2)  # wait for stabilization

                # Synchronize and update facet means
                sync_success = await self.synchronize(True)
                if not sync_success:
                    await self.synchronize(False)
                    self.enable_steppers = False
                    await self.set_error(
                        "Laser synchronization failed: photodiode lock could not be established. Aborting print job."
                    )

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
                scan_axis = self.cfg.motor_cfg["orth2lsrline"]
                axis_idx = ["x", "y", "z"].index(scan_axis)
                steps_per_mm = self.cfg.motor_cfg["steps_mm"][scan_axis]
                mm_per_facet = (1.0 / exposures) / steps_per_mm

                for lane in range(lanes):
                    if await self.handle_pausing_and_stopping():
                        await self.write_line([])
                        break
                    self.state["job"]["currentline"] = int(lane * facets_lane)
                    self.state["job"]["printingtime"] = round(time() - start_time)
                    await self.notify_listeners()
                    logger.info(f"Exposing lane {lane + 1} from {lanes}.")
                    if lane > 0:
                        logger.info("Moving in y-direction for next lane.")
                        await self.gotopoint(
                            [0, -lane_width, 0], absolute=False, check_sensors=False
                        )
                    lane_start_x = float(self._position[axis_idx])
                    direction_sign = 1 if (lane % 2 == 0) else -1
                    if lane % 2 == 1:
                        logger.info("Start exposing backward lane.")
                    else:
                        logger.info("Start exposing forward lane.")

                    total_facets = int(self.cfg.laser_timing["rpm"] / exposures)
                    if self.state["job"]["singlefacet"]:
                        total_facets = int(total_facets / 4)

                    if exposures == 1:
                        lines_chunk = self.cfg.hdl_cfg.lines_chunk
                        for facet in range(0, facets_lane, lines_chunk):
                            if facet % 1000 == 0:
                                self._position[axis_idx] = lane_start_x + (
                                    direction_sign * facet * mm_per_facet
                                )
                                self._save_position()
                                self.state["job"]["currentline"] = (
                                    int(lane * facets_lane) + facet
                                )
                                self.state["job"]["printingtime"] = round(
                                    time() - start_time
                                )
                                await self.notify_listeners()
                                if await self.handle_pausing_and_stopping():
                                    await self.set_error("Print job cancelled by user.")
                                    await self.write_line([])
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
                            if facet % 1000 == 0:
                                self._position[axis_idx] = lane_start_x + (
                                    direction_sign * facet * mm_per_facet
                                )
                                self._save_position()
                                self.state["job"]["currentline"] = (
                                    int(lane * facets_lane) + facet
                                )
                                self.state["job"]["printingtime"] = round(
                                    time() - start_time
                                )
                                await self.notify_listeners()
                                if await self.handle_pausing_and_stopping():
                                    await self.set_error("Print job cancelled by user.")
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
                    self._position[axis_idx] = lane_start_x + (
                        direction_sign * facets_lane * mm_per_facet
                    )
                    self._save_position()
                    await self.write_line([])

        # disable scanhead
        await self.notify_listeners()
        logger.info("Waiting for stopline to execute.")
        await self.wait_fifo_empty()
        await self.synchronize(False)
        self.enable_steppers = False
        if (await self.fpga_state)["error"]:
            logger.info("Error detected during printing")
        logger.info(
            f"Finished exposure. Total printing time {self.state['job']['printingtime']}"
        )
        self.state["printing"] = False
        await self.notify_listeners()
