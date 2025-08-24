import unittest

from ..stepperdriver import TMC_2209
from hexastorm.config import PlatformConfig
from control.unittest_runner import run


esp32_cfg = PlatformConfig(test=False).esp32_cfg


class TestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mtr_id = 0
        cls.tmc_cfg = esp32_cfg["tmc2209"]
        cls.tmc = TMC_2209(
            pin_en=esp32_cfg["stepper_cs"],
            mtr_id=mtr_id,
            uart_dct=cls.tmc_cfg["uart"],
        )

    def test_motor_enabled_property(self):
        # Starts disabled (EN high)
        self.assertFalse(self.tmc.motor_enabled)
        self.tmc.motor_enabled = True
        self.assertTrue(self.tmc.motor_enabled)
        self.tmc.motor_enabled = False
        self.assertFalse(self.tmc.motor_enabled)

    def test_vsense(self):
        for boolean in [True, False]:
            self.tmc.vsense = boolean
            self.assertEqual(self.tmc.vsense, boolean)

    def test_microstep_resolution(self):
        for stepsize in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            self.tmc.microstep_resolution = stepsize
            self.assertEqual(self.tmc.microstep_resolution, stepsize)
            self.assertEqual(self.tmc.steps_per_revolution, 200 * stepsize)

    def test_direction_inverted(self):
        for boolean in [True, False]:
            self.tmc.direction_inverted = boolean
            self.assertEqual(self.tmc.direction_inverted, boolean)

    def test_spread_cycle(self):
        for flag in (True, False):
            self.tmc.spread_cycle = flag
            self.assertEqual(self.tmc.spread_cycle, flag)

    def test_iscale_analog(self):
        for flag in (True, False):
            self.tmc.iscale_analog = flag
            self.assertEqual(self.tmc.iscale_analog, flag)

    def test_internal_rsense(self):
        for flag in (True, False):
            self.tmc.internal_rsense = flag
            self.assertEqual(self.tmc.internal_rsense, flag)

    def test_interpolation(self):
        for flag in (True, False, True):
            self.tmc.interpolation = flag
            self.assertEqual(self.tmc.interpolation, flag)

    def test_ifcnt_increments_on_writes(self):
        start = self.tmc.ifcnt
        self.tmc.direction_inverted = True
        self.tmc.interpolation = True
        self.tmc.vsense = True
        self.assertGreaterEqual(self.tmc.ifcnt, start + 3)

    def test_hardening(self, trials=10):
        for _ in range(trials):
            for key, value in self.tmc_cfg["settings"]:
                setattr(self.tmc, key, value)
                if key != "current":
                    self.assertEqual(getattr(self.tmc, key), value)


run(globals())
