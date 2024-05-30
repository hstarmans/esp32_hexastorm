import os

import unittest

from .. import constants
from .. import bootlib


class Update(unittest.TestCase):
    def test_version(self):
        constants.CONFIG["github"]["version"] = "10.00"
        self.assertDictEqual(bootlib.get_firmware_dct(), {})
        self.assertNotEqual(bootlib.get_firmware_dct(False), {})
        constants.CONFIG["github"]["version"] = "0.00"
        self.assertNotEqual(bootlib.get_firmware_dct(), {})
        self.assertNotEqual(bootlib.get_firmware_dct(False), {})

    def test_update_firmware(self):
        constants.CONFIG["github"]["version"] = "10.00"
        self.assertEqual(bootlib.update_firmware(), False)
        self.assertNotEqual(bootlib.update_firmware(force=True), False)
        constants.CONFIG["github"]["version"] = "0.00"
        self.assertNotEqual(bootlib.update_firmware(), False)
        fld = constants.CONFIG["github"]["storagefolder"]
        for f in os.listdir(fld):
            os.remove(fld + "/" + f)


if __name__ == "__main__":
    unittest.main()
