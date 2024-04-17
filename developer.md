# ESP32 

# Tools
I use mpremote to flash the board.

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
The next step depend on the exact chip family you use for the ESP32. It might require a manual operation or not.
Overall, the ESP32 needs to enter boot mode. Set the boot pin to low and toggle the reset pin.
Boards with a serial controller can be put in programmer mode without toggling the pins and do this automatically. They can require a different baud rate.
Boards without pins require a manual operation, release the boot pin. The board should enter boot mode. This typically causes the device to connect to a different com port. COM9 implies /dev/ttyS9 in the linux WSL layer. 
Especially older boards might run out of memory if you do not freeze in Python. If there is a modules folder, these files are frozen in.
The following is probably needed for the frozen modules (not sure).
```bash
$ cp -r modules micropython/ports/esp32/
```
Install Micropython normally, link with TMCStepper for stepper support.
```bash
$ cd micropython
$ cd mpy-cross
$ make
$ cd ..
$ cd ports/esp32
$ make submodules
$ make USER_C_MODULES=../../../../micropython.cmake
```
For the final make make the following changes.
Set in MakeFile ```PORT ?= /dev/ttyS9```  
Set in ESP32_GENERIC_S3/sdkconfig.board.  
```CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y```  
```CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-32MiB-ota.csv"```  
```CONFIG_ESPTOOLPY_OCT_FLASH=y```  
Build the board as follows;  
```make BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=SPIRAM_OCT erase```  

## Tips
You have to run ```make clean``` after ```make submodules```,
if you change the cmake file. You don't need to run ```make submodules```
each time.


## Screen + controller
A library is available [here](https://github.com/peterhinch/micropython-micro-gui)
It also outlines which screen are supported.


## Known Issues current developer board

- Pin 1 and 3 are connected to the FPGA and the UART output of the REPL.
You therefore need to connect to Web REPL, to flash the FPGA.
- Copy partitions-4MiB.csv over the existing file in micropython/ports/esp32. 
A slightly larger factory partition is needed to accommodate all the 
C++ libraries.
```bash
$ cp partitions-4MiB.csv micropython/ports/esp32/
```