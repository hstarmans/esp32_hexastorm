from machine import Pin, SoftSPI
from time import sleep

import unittest

from ..laserhead import LASERHEAD


class Hardware(unittest.TestCase):
    def test_spirepeat(self, flash=False):
        """test communciation with FPGA via SPI

        create the required binary via
        hexastorm/old/debug_spi/test_spi.py
        """
        if flash:
            # generated via old/debug_spi/test_spi
            LASERHEAD.flash("reply.bit")
        print("The red led should be on")
        bts = [0, 210, 222, 230]
        LASERHEAD.host.reset(blank=False)
        previous_byte = None
        for idx, byte in enumerate(bts):
            LASERHEAD.host.chip_select.value(0)
            sleep(1)
            response = bytearray([0])
            data = bytearray([byte])
            LASERHEAD.host.spi.write_readinto(data, response)
            byte_received = response[0]
            LASERHEAD.host.chip_select.value(1)
            if idx != 0:
                try:
                    assert previous_byte == byte_received
                except AssertionError:
                    print(previous_byte)
                    print(byte_received)
                    raise Exception("Test failed: not equal")
            previous_byte = byte


    def test_write_blink_toflash(self):
        """write blink test to flash ram and check blinking

        FPGA is placed in reset
        File is written to flash ram
        FPGA reset is released and should program itself
        """
        LASERHEAD.flash("blink.bit")
        res = input("Test wether led blinks, input y and enter for succes\n")
        assert res == "y"


if __name__ == "__main__":
    unittest.main()
