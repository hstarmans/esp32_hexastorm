# ESP32


# Development Notes
The following is a list of notes of things I figured out so far.

## Getting Micropython
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

# Fomuflash
I made a frozen module for Fomuflash. 
This is used to flash a memory which can be used to program the FPGA chip.

# TMCStepper
I use TMC2130 stepper drivers. These drivers have to be configured before they can be used.
To fix this I made a frozen module for the TMC stepper drivers.


## Webserver
I dropped the idea for webserver. For now I want to work via the rpython shell.
This simplifies development.
I did get one running but it is not longer updated.
Originally, my idea was to work with a webserver on the ESP32.
An example is shown here [MicroWebSrv2](https://github.com/jczic/MicroWebSrv2).
This idea has been dropped for now.

You need to compile MicroPython with [MicroWebSrv2](https://github.com/jczic/MicroWebSrv2). 
Build the generic version of MicroPython.
Place MicroWebSrv2, only the MicroWebSrv2 subfolder, into the modules directory. This is known as a frozen [module](https://learn.adafruit.com/micropython-basics-loading-modules/frozen-modules).
Also place sdcard.py in the modules directory, located in micropython/drivers.
Recompile, check the module is cross compiled, erase the flash
and flash the new binary to the memory.

# Pinout

Pinout of the ESP32 can be found [here](https://microcontrollerslab.com/esp32-pinout-use-gpio-pins/).
Accessing the ports is outlined in the [quickref](https://docs.micropython.org/en/latest/esp32/quickref.html).

