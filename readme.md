# ESP32

## Creating a binary
As described on [esp32](https://github.com/micropython/micropython/tree/master/ports/esp32).
I start by install espd-idf.
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
Hereafter I install Micropython.
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


Follow the instructions outlined [here](https://docs.micropython.org/en/latest/develop/gettingstarted.html). 
Clone micropython, build the cross-compiler and go to
the ports/esp32 directory. 
In the readme located in ports/esp32, you will find the instruction on how to download and use esp-idf.
On Ubuntu you might need,
```sudo apt-get install python-is-python3```  
Ubuntu is required. On Windows, Ubuntu WSL 2 cannot communicate with com port.
This makes development on Windows more difficult.

## Communicating with Micropython
A usefull tool to interact with the ESP32 is [rshell](https://github.com/dhylands/rshell).  
You can connect with the esp32 via
```connect serial /dev/ttyUSB0``` or
```connect serial com6``` on Windows.
The flash memory is available as /flash or /pyboard.
```repl``` gives you an interactive developer environment with
the board.
Esptool can also be used to flash and erase the ESP32. 

# SD Card
I made a board with a ESP32 microcontroller and a SD CARD.
Too mount the SD card, I wrote some additional code. This
is not needed in the latest version of the Hexastorm, as the
the ESP32-S3-WROOM-2 has 32 MB onboard memory.

# Fomuflash
I made a frozen module for Fomuflash. 
This is used to flash a memory which can be used to program the FPGA chip.

# TMCStepper
I use TMC2130 stepper drivers. These drivers have to be configured before they can be used.
To fix this I made a frozen module for the TMC stepper drivers.


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

