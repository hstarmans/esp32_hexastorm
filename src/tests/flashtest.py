# Flash test script
# Stand alone script to
# test detection of flash
from winbond import W25QFlash
from machine import SoftSPI, Pin
from time import sleep

spi = SoftSPI(baudrate=int(1e6),
    polarity=1,
    phase=0,
    sck=Pin(12, Pin.OUT),
    mosi=Pin(13, Pin.OUT),
    miso=Pin(11, Pin.IN),
)
flash_select = Pin(10, Pin.OUT)
flash_select.value(0)
fpga_select = Pin(9, Pin.OUT)
fpga_select.value(0)
fpga_reset = Pin(47, Pin.OUT)
fpga_reset.value(0)

flash_select.value(1)
sleep(1)
print("trying to detect")
f = W25QFlash(
    spi=spi,
    cs=flash_select,
    baud=int(1e6),
    software_reset=True,
)
print("detected")

