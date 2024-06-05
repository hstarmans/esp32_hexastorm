from machine import Pin, SoftSPI
from time import sleep

import unittest

from ..laserhead import LASERHEAD

import steppers


class Hardware(unittest.TestCase):
    def test_steppers_lib(self):
        """test stepper library

        This disconnects you from the REPL
        """
        # this produces output in REPL and show wether connection succeeded
        steppers.init()
        enable = Pin(3, Pin.OUT)
        enable(0)
        res = input(
            "Test wether axis are fixed, input y and enter for succes\n"
        )
        enable(1)
        assert res == "y"

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

    def test_steppers_nolib(self):
        """write to chopconfig and verify data is returned

        this test should work, advantage is that it does not
        require external libraries.
        You can turn of the 12V power to the motors, it should then fail.
        see example on page 22
        https://www.trinamic.com/fileadmin/assets/Products/ICs_Documents/TMC2130_datasheet.pdf
        If more than 40 bits are sent, only the last 40 bits received before
        the rising edge of CSN are recognized as the command.
        The rest are shifted on in the ring.
        There are three drivers in the ring.
        """
        baudrate = int(1e6)

        # ENABLE PIN, is pin 3
        # Crashrepl
        # ce = Pin(3, Pin.OUT, Pin.PULL_UP)
        cs = Pin(2, Pin.OUT, Pin.PULL_UP)

        spi = SoftSPI(
            baudrate=int(baudrate), sck=Pin(4), mosi=Pin(16), miso=Pin(17)
        )

        txdata = bytearray([0xEC, 1, 2, 3, 4] * 3)
        rxdata = bytearray(len(txdata))
        # 0XEC = 236 != 249 i.e. data is altered
        expected = bytearray([249, 1, 2, 3, 4] * 3)
        for _ in range(2):
            try:
                cs(0)
                spi.write_readinto(txdata, rxdata)
            finally:
                cs(1)
        # mosi pin on your board seems loose
        assert expected == rxdata

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
