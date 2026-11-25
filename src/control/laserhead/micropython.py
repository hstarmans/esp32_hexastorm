import logging

from hexastorm.fpga_host.micropython import ESP32Host
from hexastorm.fpga_host.syncwrap import syncable
from hexastorm.fpga_host.tools import find_shift

from .. import constants
from .base import BaseLaserhead

logger = logging.getLogger(__name__)


class Laserhead(ESP32Host, BaseLaserhead):
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
        num_facets = int(self.cfg.laser_timing["facets"])
        if self.cur_facet_means is None:
            if (await self.fpga_state)["synchronized"]:
                self.cur_facet_means = await self.measure_facet_means()
            else:
                mapped = int(facet_id) % num_facets
                logger.warning(
                    f"Facet remap called without facet timing means; returning {mapped}."
                )
                return mapped

        # Calculate how many positions the facets have rotated and goodness-of-fit metrics
        shift, corr, _rmse = find_shift(self.cur_facet_means, self.facet_means)

        # Apply the shift to find the new position
        current_index = int((facet_id + shift) % num_facets)

        logger.info(
            f"Facet remap: facet_id {facet_id} mapped to index {current_index} (shift: {shift}, correlation: {corr:.4f})."
        )

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

    async def enable_comp(
        self,
        laser0=None,
        laser1=None,
        polygon=None,
        synchronize=None,
        singlefacet=None,
    ):
        """enable components

        laser0      -- True enables laser channel 0
        laser1      -- True enables laser channel 1
        polygon     -- False enables polygon motor
        synchronize -- Enables photodiode synchronization
        singlefacet -- Enables single-facet mode
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

    async def gotopoint(
        self,
        position,
        speed=None,
        absolute=True,
        check_sensors=False,
        workspace=False,
        validate_limits=True,
    ):
        """
        Wraps the hardware gotopoint function to automatically handle stepper enabling
        and state notifications for the web UI.
        """
        # Validate soft limits before sending move to hardware
        if validate_limits:
            self._validate_target_position(
                position, absolute=absolute, workspace=workspace
            )

        # 1. Execute hardware command (blocks until physical move is complete)
        result = await ESP32Host.gotopoint(
            self,
            position=position,
            speed=speed,
            absolute=absolute,
            workspace=workspace,
            check_sensors=check_sensors,
            validate_limits=validate_limits,
        )

        # 2. Update RAM and NVS instantly using the synchronous helper from base.py
        self._save_position()

        # 3. Trigger SSE update for the web clients
        await self.notify_listeners()
        return result

    async def emergency_stop(self):
        """Hardware emergency stop: holds fpga_reset LOW to reset FPGA logic & FIFOs."""
        logger.warning("Hardware EMERGENCY ABORT triggered!")
        if hasattr(self, "fpga_reset") and self.fpga_reset is not None:
            self.fpga_reset.value(0)
        await super().emergency_stop()

    async def reset_estop(self):
        """Re-flash FPGA bitstream, restore SPI/steppers, and clear E-Stop alarm state."""
        logger.info("Resetting E-Stop state on hardware...")
        try:
            if hasattr(self, "fpga_reset") and self.fpga_reset is not None:
                self.fpga_reset.value(1)
            if hasattr(self, "reset"):
                self.reset()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error during hardware reset: {e}")
        await super().reset_estop()

    async def home_axes(self, axes, speed=None, displacement=200, pull_off=10):
        if self.state.get("estop", False):
            raise ValueError("Machine is locked in E-STOP state! Reset machine first.")
        logger.info(f"Homing axes {axes}.")

        for tmc in self.steppers.values():
            tmc.spread_cycle = False

        try:
            await ESP32Host.home_axes(
                self,
                axes,
                speed=speed,
                displacement=displacement,
                pull_off=pull_off,
            )
        finally:
            for tmc in self.steppers.values():
                tmc.spread_cycle = True

        self._save_position()
        await self.notify_listeners()

    async def set_workspace_zero(self, axes=None):  # pyrefly: ignore[bad-override]
        # 1. Allow the FPGA/hardware layer to reset its internal offsets
        ESP32Host.set_workspace_zero(self, axes)

        # 2. Update the python state tracking and save to NVS
        self._update_workspace_zero(axes)
        await self.notify_listeners()

    async def set_spindle(self, value: "int | float"):
        await BaseLaserhead.set_spindle(self, value)
        await self.set_spindle_speed(self.state["components"]["spindle"])
        await self.notify_listeners()

    async def set_fan(self, value: "int | float"):
        await BaseLaserhead.set_fan(self, value)
        await self.set_fan_speed(self.state["components"]["fan"])
        await self.notify_listeners()

    async def synchronize(self, value=True):
        """Synchronize laser with phodiode.

        Args:
            value (bool): True to enable synchronization, False to disable.
        """
        res = await super().synchronize(value)
        if value and res:
            self.cur_facet_means = await self.measure_facet_means()
        else:
            self.cur_facet_means = None
        return res

    async def test_diode(self):
        logger.debug("Starting diode test.")
        self.state["components"]["diodetest"] = None
        await self.notify_listeners()

        fpga_st = await self.fpga_state
        cur_sync = fpga_st["synchronized"]
        cur_polygon = bool(self._pin_state & (1 << 2))

        if not cur_sync:
            sync_success = await self.synchronize(True)
            if not sync_success:
                logger.error("Diode test failed: Laser cannot be synchronized.")
                self.state["components"]["diodetest"] = {
                    "passed": False,
                    "error_reason": "Laser cannot be synchronized",
                    "global_mean_ms": 0.0,
                    "global_deviation_perc": 0.0,
                    "expected_rpm": self.cfg.laser_timing["rpm"],
                    "measured_rpm": 0,
                    "facets": {},
                }

                # Restore synchronization and motor to their original states
                await self.enable_comp(polygon=cur_polygon)

                await self.notify_listeners()
                return
        if self.cur_facet_means is None:
            self.cur_facet_means = await self.measure_facet_means()
        shift = find_shift(self.cur_facet_means, self.facet_means)[0]

        # 2. Pass shift and stored calibration reference down to the parent
        self.state["components"]["diodetest"] = await super().test_laserhead(
            shift=shift,
            ref_facet_means=self.facet_means,
        )

        # Restore synchronization and motor to their original states

        if not cur_sync:
            await self.synchronize(False)
            await self.enable_comp(polygon=cur_polygon)
        await self.notify_listeners()


@syncable
class LaserheadSync(Laserhead):
    def __init__(self, sync=True):
        super().__init__()
