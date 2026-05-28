import machine
from time import sleep
import time
from machine import Pin

# --- Configuration ---
# configuration also works with fpga but you have to flip lines
PIN_CRESET = 43
PIN_CDONE = 44
PIN_CS = 2
PIN_SCK = 6
PIN_MOSI = 4
PIN_MISO = 5

# Initialize Pins
creset = machine.Pin(PIN_CRESET, machine.Pin.OUT)
cdone = machine.Pin(PIN_CDONE, machine.Pin.IN, machine.Pin.PULL_UP)
cs = machine.Pin(PIN_CS, machine.Pin.OUT)

creset.value(0)
cs.value(1)

sleep(1)

# Fix 1: Corrected the MISO typo
spi = machine.SPI(
    2,
    baudrate=1000000,
    polarity=0,
    phase=0,
    sck=Pin(PIN_SCK),
    mosi=Pin(PIN_MOSI),
    miso=Pin(PIN_MISO),
)


def program_fpga(filename):
    print("Start FPGA upload...")

    # 1. Handshake: Force SPI Slave Mode
    cs.value(1)
    creset.value(0)
    time.sleep_ms(500)  # 10ms is plenty

    cs.value(0)  # Pull CS low
    time.sleep_ms(500)
    creset.value(1)  # Release CRESET (FPGA samples CS low and enters Slave mode)
    time.sleep_ms(500)  # Wait for internal memory clear (min 1200us)

    # Fix 3: Send 8 dummy clocks BEFORE the bitstream.
    spi.write(b"\x00")
    # This wakes up the FPGA config interface and keeps the Winbond asleep.
    # 2. Send Bitstream
    with open(filename, "rb") as f:
        while True:
            chunk = f.read(512)
            if not chunk:
                break
            spi.write(chunk)

    # 3. Finish configuration
    # Send a few clocks while CS is still low
    spi.write(bytes([0x00] * 10))

    # Fix 4: Raise CS and send >49 dummy clocks (13 bytes = 104 clocks)
    cs.value(1)
    time.sleep_ms(500)
    spi.write(bytes([0x00] * 13))

    time.sleep_ms(500)  # Give CDONE a moment to pull up

    if cdone.value():
        print("FPGA succesvol geprogrammeerd!")
    else:
        print("FPGA boot mislukt. CDONE bleef laag.")


program_fpga("blinky.bin")
