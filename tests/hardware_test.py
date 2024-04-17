# File probably needs to be added to the controller class
# at some point
# Two things are tested; can I write to Flash and
# can I initiate the TMC2130 stepper drivers
# requires winbon 0.5.2 (see https://github.com/brainelectronics/micropython-winbond/pull/9)

from machine import Pin, SoftSPI
from time import sleep


def test_steppers_lib():
    """test stepper library

    This disconnects you from the REPL
    """
    import steppers

    # this produces output in REPL and show wether connection succeeded
    steppers.init()
    enable = Pin(3, Pin.OUT)
    enable(0)
    input("Test wether axis are fixed and press enter")
    enable(1)


def test_steppers_nolib():
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

    spi = SoftSPI(baudrate=int(baudrate), sck=Pin(4), mosi=Pin(16), miso=Pin(17))

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
    print("Test succeeded")


def write_toflash(url):
    """write file to flash ram

    FPGA is placed in reset
    File is written to flash ram
    FPGA is pulled outof reset and should program itself

    url:  path to file
    """
    fpga_reset = Pin(1, Pin.OUT)

    # FPGA in active reset
    baudrate = int(1e6)
    spi = SoftSPI(
        baudrate=int(baudrate),
        polarity=1,
        phase=0,
        sck=Pin(32),
        mosi=Pin(25),
        miso=Pin(26),
    )
    cs = Pin(27)

    from winbond import W25QFlash

    f = W25QFlash(spi=spi, cs=cs, baud=int(baudrate), software_reset=True)

    fpga_reset.value(0)
    buffsize = f.BLOCK_SIZE
    # if dest.endswith("/"):  # minimal way to allow
    #    dest = "".join((dest, source.split("/")[-1]))  # cp /sd/file /fl_ext/
    with open(url, "rb") as infile:
        blocknum = 0
        while True:
            buf = infile.read(buffsize)
            print(f" Writing {blocknum}.")
            f.writeblocks(blocknum, buf)
            if len(buf) < buffsize:
                print(f"Final block {blocknum}")
                break
            else:
                blocknum += 1
    sleep(1)
    fpga_reset.value(1)
