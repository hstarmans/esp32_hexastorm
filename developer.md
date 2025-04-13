# ESP32 
Webserver is roughly deployed as follows. A binary is generated, this binary is used to flash the ESP32S3.
After flashing the ESP32, the user logs on to the ESP32 using Thonny. 
Using the micropython shell, the user changes config.json and updates the wifi password. 
The user programs the FPGA (only needed once) and launches the webserver.
After cloning the repository also pull in the git lfs objects.
```bash
git clone https://github.com/hstarmans/esp32_hexastorm
git lfs fetch --all
``` 

## Python dependencies
Install the dependency manager uv.
```bash
sudo apt install pipx
pipx ensurepath
pipx install uv
```
Use uv to install the dependencies in pyproject.toml.
```bash
uv sync
```
Install the hexastorm libarry somewhere and symlink to it
The hexastorm library is imported using a symlink. This is seen as easier than using git submodules.
```bash
ln -s ~/python/hexastorm/src/hexastorm/ src/hexastorm
```
Create the folder src/sd/jobs/.

### Testing code in python
The webserver can be tested via, 
```bash
uv run python -m src.control.webapp
```
Default password is "hex".
You can run tests via the command below. The s-flag ensures print statements are directed to the shell.
This test requires additonial steps, please refer to the code.
```bash
uv run pytest -s --pyargs tests.test_webserver::test_websocket
```

### Testinc code in micropython

Prior to testing execute
```bash
cd src
```
Install micropython on linux with [ulab](https://github.com/v923z/micropython-ulab). Run tests via
```bash
micropython -m test.test_hardware
```
```
This is tested as follows
```bash
micropython -m test.test_hexastorm LaserheadTest test_stable
```

## Creating a binary for the ESP32 microcontroller
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

# Tools
Mpremote is supported by Micropython. I tried [Mpfshell](https://github.com/wendlers/mpfshell) 
and [rshell](https://github.com/dhylands/rshell). [Thonny](https://thonny.org/) offers best webrepl support.


### Tips
You have to run ```make clean``` after ```make submodules```,
if you change the cmake file. You don't need to run ```make submodules```
each time.
