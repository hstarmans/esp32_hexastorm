# File probably needs to be added to the controller class 
# at some point
# Two things are tested; can I write to Flash and 
# can I initiate the TMC2130 stepper drivers
# requires winbon 0.5.2 (see https://github.com/brainelectronics/micropython-winbond/pull/9)

from machine import Pin, SoftSPI
from time import sleep

fpga_reset = Pin(1, Pin.OUT)

# FPGA in active reset
baudrate=int(1E6)
spi = SoftSPI(baudrate=int(baudrate), 
              polarity=1, 
              phase=0, 
              sck=Pin(32), 
              mosi=Pin(25), 
              miso=Pin(26))
cs = Pin(27)

from winbond import W25QFlash
f = W25QFlash(spi=spi, 
              cs=Pin(27), 
              baud=int(baudrate), 
              software_reset=True)


def write_toflash(url):
    '''write file to flash ram

    FPGA is placed in reset
    File is written to flash ram
    FPGA is pulled outof reset and should program itself

    url:  path to file
    '''
    fpga_reset.value(0)
    buffsize = f.BLOCK_SIZE 
    #if dest.endswith("/"):  # minimal way to allow
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

