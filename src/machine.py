# Testing class
#     Debugging class for testing code in linux.

def reset():
    print("resetting entire chip")

class I2C:
    def __init__(self, id=0, scl=1, sda=2, freq=400000, timeout=50000):
        self.id = id

    def init(self, controller, address):
        self.address = address
        self.CONTROLLER = controller

    def writeto(self, address, bytes):
        pass

    def readfrom(self, address, number_bytes):
        return bytearray([1])


class SoftI2C(I2C):
    pass


class UART:

    def __init__(self, serialport, baudrate=115200, tx=43, rx=44):
        pass

    def init(self, speed=115200 , bits=8, parity=None, stop=1):
        pass

    def write(self, data):
        return data

    def read_int(self, offset):
        return []

    def close(self):
        pass


class Pin:
    OUT = 0
    IN = 1
    PULL_UP = 1

    def __init__(self, id, mode=-1, pull=-1, value=4):
        self.init(id, mode, pull, value)

    def init(self, id=1, mode=-1, pull=-1, value=4):
        self.id = id
        self.mode = mode
        self.pull = pull

    def on(self):
        pass

    def off(self):
        pass

    def cleanup():
        pass

    def value(self, val):
        self.val = val

    def __call__(self, val):
        self.value(val)


class SoftSPI:
    def __init__(
        self, sck=1, mosi=2, miso=2, baudrate=100000, polarity=1, phase=0
    ):
        self.init(sck, mosi, miso, baudrate, polarity, phase)

    def init(
        self, sck=2, mosi=-1, miso=1, baudrate=100_000, polarity=1, phase=0
    ):
        self.sck = sck
        self.mosi = mosi
        self.miso = miso
        self.baudrate = baudrate
        self.polarity = polarity
        self.phase = phase

    def write_readinto(self, txdata, rxdata):
        # very specific for test_stepper_nolib
        # TMC stepper alters 5 th byte
        for i, _ in enumerate(txdata):
            if i % 5 == 0:
                rxdata[i] = 249
            else:
                rxdata[i] = txdata[i]

    def write(self, data):
        pass

    # not sure
    def read(self, nbytes=3, write=0x00):
        # gets device id in case winbond memory
        if write == 0x00:
            return [0xEF, 0x40, 10]

        return [0] * nbytes


class SPI:
    def __init__(self, number=0, sck=2, mosi=3, miso=4, baudrate=100_000, phase=-1, polarity=-1):
        self.init(number, baudrate, phase, polarity)

    def init(self, number=2, baudrate=10, phase=-1, polarity=-1):
        self.number = number
        self.baudrate = baudrate
        self.phase = phase
        self.polarity = polarity

    def deinit(self):
        pass

    def write_readinto(self, txdata, rxdata):
        # very specific for test_stepper_nolib
        # TMC stepper alters 5 th byte
        for i, _ in enumerate(txdata):
            rxdata[i] = txdata[i]
