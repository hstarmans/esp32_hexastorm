# ESP32

Control a laser scanner build using the [Hexastorm](https://github.com/hstarmans/hexastorm) library with an ESP32.

## Creating a binary
I follow the procedure described on [esp32](https://github.com/micropython/micropython/tree/master/ports/esp32).
I start by installing espd-idf.
```bash
$ git clone https://github.com/hstarmans/esp32_hexastorm
$ git submodule update --init
$ cd esp-idf
$ git checkout v5.0.2
$ ./install.sh       # (or install.bat on Windows)
$ source export.sh   # (or export.bat on Windows)
$ cd.. # back to starting directory
```
The `install.sh` step only needs to be done once. You will need to source
`export.sh` for every new session.
Copy partitions-4MiB.csv over the existing file in micropython/ports/esp32. 
A slightly larger factory partition is needed to accommodate all the 
C++ libraries.
```bash
$ cp partitions-4MiB.csv micropython/ports/esp32/
```
The code in the folder sdcard must be copied to the microSD card which
is inserted in the ESP32 board. You have to do this your self.
Change the wifi password in secrets.py before doing so.
The code in the modules folder is [frozen](https://learn.adafruit.com/micropython-basics-loading-modules/frozen-modules) 
into micropython.
```bash
$ cp -r modules micropython/ports/esp32/
```
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
You have to run ```make clean``` after ```make submodules```,
if you change the cmake file. You don't need to run ```make submodules```
each time.

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
The sdcard is available if it is mounted successfully, see boot.py.

# Pinout
Pinout of the ESP32 board I used for this code, can be found [here](https://microcontrollerslab.com/esp32-pinout-use-gpio-pins/).
Accessing the ports is outlined in the [quickref](https://docs.micropython.org/en/latest/esp32/quickref.html).


## Webserver
Developing a webserver is not a priority. For now, I want to work via the rpython shell.
Best option for the webserver seems to be [microdot](https://github.com/miguelgrinberg/microdot/tree/main)
Another interesting opption is [MicroWebSrv2](https://github.com/jczic/MicroWebSrv2).
This webserver is no longer under active development.
