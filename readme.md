# ESP32

## Creating a binary
I follow the procedure desribed on [esp32](https://github.com/micropython/micropython/tree/master/ports/esp32).
I start by installing espd-idf.
```bash
$ cd esp-idf
$ git checkout v5.0.2
$ git submodule update --init --recursive
$ ./install.sh       # (or install.bat on Windows)
$ source export.sh   # (or export.bat on Windows)
$ cd.. # back to starting directory
```
The `install.sh` step only needs to be done once. You will need to source
`export.sh` for every new session.
Copy partitions-4MiB.csv over the existing file in micropython/ports/esp32. 
A slightly larger factory partition is needed to accommodate all the 
C++ libraries.
Hereafter I install Micropython and build the binary.
```bash
$ cd micropython
$ cd mpy-cross
$ make
$ cd ..
$ cd ports/esp32
$ make submodules
$ make USER_C_MODULES=../../../../micropython.cmake
```
You have to run ```make erase``` after ```make submodules```,
if you change the cmake file. 

## Flashing the binary
The following erases and flashes the binary to the chip.
```bash
$ make erase && make deploy
```
On linux, ensure you are part of the dial out group.
Development on Windows can be more difficult.
You might not have the rights to write to the com port. 
Install [rshell](https://github.com/dhylands/rshell) and open it.
```bash
sudo pip3 install rshell
rshell
connect serial /dev/ttyUSB0
```
On Windows it can be ```connect serial com6```.
The flash memory is available as /flash or /pyboard.
The sdcard is avaible if it is mounted successfully, see boot.py.

# Fomuflash
I made a frozen module for Fomuflash. 
This is used to flash a memory which programs the FPGA chip. Once the binary is installed
it can be reached via
```import fomuflash```

# TMCStepper
I use TMC2130 stepper drivers. These drivers have to be given certain settings before they can
be used.
```import tmcstepper```

## Webserver
Developing a webserver is not a priority. For now, I want to work via the rpython shell.
Best option for the webserver seems to be [microdot](https://github.com/miguelgrinberg/microdot/tree/main)
Another interesting opption is [MicroWebSrv2](https://github.com/jczic/MicroWebSrv2).
This webserver is no longer under active development.

Place MicroWebSrv2, only the MicroWebSrv2 subfolder, into the modules directory. This is known as a frozen [module](https://learn.adafruit.com/micropython-basics-loading-modules/frozen-modules).
Also place sdcard.py in the modules directory, located in micropython/drivers.
Recompile, check the module is cross compiled, erase the flash
and flash the new binary to the memory.

# Pinout
Pinout of the ESP32 board I used for this code, can be found [here](https://microcontrollerslab.com/esp32-pinout-use-gpio-pins/).
Accessing the ports is outlined in the [quickref](https://docs.micropython.org/en/latest/esp32/quickref.html).

