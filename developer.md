# ESP32 

# Tools
Mpremote is supported by Micropython. I tried [Mpfshell](https://github.com/wendlers/mpfshell) 
and [rshell](https://github.com/dhylands/rshell). [Thonny](https://thonny.org/) offers best webrepl support.

## Python dependencies
Install poetry in the directory in which you installed this library.
```bash
sudo apt install python3 python3-pip
curl -sSL https://install.python-poetry.org | python3 -
```
Use poetry to install the dependencies in pyproject.toml.
The virtual environment needs to be installed in project for VSCode to detect it.
```bash
poetry config virtualenvs.in-project true
poetry install
```

## Creating a binary
I follow the procedure described on [esp32](https://github.com/micropython/micropython/tree/master/ports/esp32).
First install esp-idf and activate it. Git cannot be able to download it.
```bash
git clone -b v5.2.2 --recursive https://github.com/espressif/esp-idf.git$ 
cd esp-idf
./install.sh       # (or install.bat on Windows)
source export.sh   # (or export.bat on Windows)
```
The `install.sh` step only needs to be done once. You will need to source
`export.sh` for every new session.
Hereafter, clone ulab, my stepper library, switch to the ESP32 branch.
```bash
git clone https://github.com/v923z/micropython-ulab
```
Install Micropython normally, please note that micropython-ulab, TMCStepper and micropython should
be in the same root folder.
```bash
git clone https://github.com/micropython/micropython.git
cd micropython
cd mpy-cross
make
cd ..
cd ports/esp32
make submodules
```
The next step depends on the exact chip family you use for the ESP32. It might require a manual operation or not.
Overall, the ESP32 needs to enter boot mode. Set the boot pin to low and toggle the reset pin.
This typically causes the device to connect to a different com port. COM9 implies /dev/ttyS9 in the linux WSL layer.
On windows devices can be seen via the DeviceManager, on linux use dmesg -d.
Boards with a serial controller can be placed in programmer mode without toggling the pins and do this automatically. Boards can require a different baud rate, e.g. 115200 for ESP32 devkit 1.  
For the ESP32S3, we use the 32 megabyte version and I use the partition which supports over the air updates.  
Set in ESP32_GENERIC_S3/sdkconfig.board.  
```
CONFIG_BOOTLOADER_ROLLBACK_ENABLE=y
CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-32MiB-ota.csv
CONFIG_ESPTOOLPY_OCT_FLASH=y
```  
Copy micropython.cmake from this repo to the root folder of micropython-ulab, TMCStepper and micropython.
Build the board as follows.
Add erase and deploy the board at the end of this command.
```erase``` and ```deploy```  
```bash
make BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=SPIRAM_OCT PORT=/dev/ttyS16 FROZEN_MANIFEST=/home/hstarmans/python/esp32_hexastorm/src/manifest.py USER_C_MODULES=../../../../micropython.cmake
```
After building you might want to create a secrets.json by cloning config.json, fill in the ESSID and password and copy it to the ESP32 via
```bash
poetry run python utility.py --device /dev/ttyS8
```

### Tips
You have to run ```make clean``` after ```make submodules```,
if you change the cmake file. You don't need to run ```make submodules```
each time.

## Testing code
You can run tests via the command below. The s-flag ensures print statements are directed to the shell.
```bash
poetry run pytest -s --pyargs tests.test_webserver::test_websocket
```
The webserver can be tested via, 
```bash
poetry run python -m src.control.webapp
```
Default password is "hex".

### Micropython Tests

Prior to testing execute
```bash
cd src
```
Install micropython on linux with [ulab](https://github.com/v923z/micropython-ulab). Run tests via
```bash
micropython -m test.test_hardware
```
The hexastorm library is imported using a symlink. This is seen as easier than using git submodules.
```bash
ln -s ~/python/hexastorm/src/hexastorm/ hexastorm
```
This is tested as follows
```bash
micropython -m test.test_hexastorm LaserheadTest test_stable
```

## Screen + controller
A library is available [here](https://github.com/peterhinch/micropython-micro-gui)
It also outlines which screens are supported.

## Known Issues current developer board
- Pin 1 and 3 are connected to the FPGA and the UART output of the REPL.
You therefore need to connect to Web REPL, to flash the FPGA.
- Copy partitions-4MiB.csv over the existing file in micropython/ports/esp32. 
A slightly larger factory partition is needed to accommodate all the 
C++ libraries.
```bash
$ cp partitions-4MiB.csv micropython/ports/esp32/
```