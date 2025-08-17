from tmc.stepperdriver import TMC_2209
from hexastorm.config import PlatformConfig
import unittest


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

    def test_vsense(self):
        for boolean in [True, False]:
            self.tmc.vsense = boolean
            self.assertEqual(self.tmc.vsense, boolean)

    def test_microstep_resolution(self):
        for stepsize in [16, 32]:
            self.tmc.microstep_resolution = stepsize
            self.assertEqual(self.tmc.microstep_resolution, stepsize)

    def test_direction_inverted(self):
        for boolean in [True, False]:
            self.tmc.direction_inverted = boolean
            self.assertEqual(self.tmc.direction_inverted, boolean)


tst = TestBase()
tst.setUpClass()
tst.test_vsense()
tst.test_microstep_resolution()
tst.test_direction_inverted()
