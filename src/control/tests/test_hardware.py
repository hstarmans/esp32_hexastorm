import unittest
from time import sleep

try:
    from typing import TYPE_CHECKING
except ImportError:
    TYPE_CHECKING = False

from ..laserhead import laserhead

if TYPE_CHECKING:
    from typing import cast

    from ..laserhead.micropython import Laserhead

    lh = cast("Laserhead", laserhead)
else:
    lh = laserhead


class Hardware(unittest.TestCase):
    """Hardware test suite for obscure hardware edge cases.

    NOTE: These tests require connected physical hardware and specific
    test bitstream binaries (e.g., reply.bit, blink.bit).
    """

    def test_spirepeat(self, flash=False):
        """test communication with FPGA via SPI

        Obscure test case: Requires special binary `reply.bit` created via
        `hexastorm/old/debug_spi/test_spi.py`.
        """
        if flash:
            # generated via old/debug_spi/test_spi
            lh.flash_fpga("reply.bit")
        print("The red led should be on")
        bts = [0, 210, 222, 230]
        lh.reset()
        previous_byte = None
        for idx, byte in enumerate(bts):
            lh.fpga_cs.value(0)
            sleep(1)
            response = bytearray([0])
            data = bytearray([byte])
            lh.spi.write_readinto(data, response)
            byte_received = response[0]
            lh.fpga_cs.value(1)
            if idx != 0:
                try:
                    assert previous_byte == byte_received
                except AssertionError:
                    print(previous_byte)
                    print(byte_received)
                    raise AssertionError("Test failed: not equal") from None
            previous_byte = byte

    def test_write_blink_toflash(self):
        """write blink test to flash ram and check blinking

        FPGA is placed in reset
        File is written to flash ram
        FPGA reset is released and should program itself
        """
        lh.flash_fpga("blink.bit")
        res = input("Test wether led blinks, input y and enter for succes\n")
        assert res == "y"


if __name__ == "__main__":
    unittest.main()
