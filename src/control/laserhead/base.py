import logging
import asyncio
from time import time
from random import randint

try:
    import numpy as np

    NP_FLOAT = float
except ImportError:
    from ulab import numpy as np

    NP_FLOAT = np.float

from hexastorm.config import PlatformConfig


from ..constants import CONFIG, NVS_STORE


logger = logging.getLogger(__name__)


class BaseLaserhead:
    def __init__(self):
        self.cfg = PlatformConfig(test=False)  # overwritten by derived classes

        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._start = asyncio.Event()
        self.statechange = asyncio.Event()
        self._debug = False

        # Load coodinates from NVS flash database

        if not hasattr(self, "_position"):
            self._position = np.array(
                [
                    NVS_STORE.get_int("mpos_x", 0) / 1000.0,
                    NVS_STORE.get_int("mpos_y", 0) / 1000.0,
                    NVS_STORE.get_int("mpos_z", 0) / 1000.0,
                ],
                dtype=NP_FLOAT,
            )

        if not hasattr(self, "_work_offset"):
            self._work_offset = np.array(
                [
                    NVS_STORE.get_int("woff_x", 0) / 1000.0,
                    NVS_STORE.get_int("woff_y", 0) / 1000.0,
                    NVS_STORE.get_int("woff_z", 0) / 1000.0,
                ],
                dtype=NP_FLOAT,
            )

        self.reset_state()

        self.reset_state()

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
            "job": job,
            "components": components,
            "mpos": self.mpos,
            "wpos": self.wpos,
        }
        self._state = state

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
        """Resets the machine coordinates to 0.0 for the specified homed axes."""
        for i in range(len(axes)):
            if axes[i] == 1:
                self._position[i] = 0.0
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

    # --- MOCK / PC ASYNC METHODS ---
    # These include simulated delays and call the synchronous helpers above.

    async def gotopoint(
        self,
        position,
        speed=None,
        absolute=True,
        workspace=False,
        check_sensors=True,
    ):
        """Simulates target movement and updates mock coordinates over time."""
        logger.info(f"Mock moving to {position} (abs={absolute}, wpos={workspace}).")

        # Simulate physical transit time (Great for UI testing!)
        await asyncio.sleep(0.3)

        self._update_coordinates(position, absolute, workspace)
        await self.notify_listeners()

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

    async def home(self, axes):
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
        total_lines = 10
        self.laser_current = self.state["job"]["laserpower"]
        self.state["job"]["totallines"] = total_lines

        # Read the workflow settings
        home_before = CONFIG["defaultprint"]["home_before_print"]
        use_custom = CONFIG["defaultprint"]["use_custom_start"]
        custom_origin = CONFIG["defaultprint"]["workspace_origin"]

        # 1. Homing check
        if home_before:
            logger.info("Homing X- and Y-axis.")
            await self.home([1, 1, 0])
        else:
            logger.info("Skipping homing before print per user settings.")

        # 2. Start position check
        if use_custom:
            logger.info(f"Overriding workspace origin to custom MPOS: {custom_origin}")
            self._work_offset = np.array(custom_origin, dtype=NP_FLOAT)
            self._save_position()

        logger.info("Moving to workspace origin (WPOS 0, 0, 0).")
        await self.gotopoint([0.0, 0.0, 0.0], absolute=True, workspace=True)

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
        # Ensure the final position is logged to NVS when the print concludes
        self._save_position()
        await self.notify_listeners()
