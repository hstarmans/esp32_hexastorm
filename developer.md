# ESP32 
The webserver deployment process involves generating a binary, flashing it to the ESP32, configuring the device, 
programming the FPGA (one-time operation), and finally launching the webserver.
Begin by cloning the project repository, ensuring you also retrieve the Git Large File Storage (LFS) objects.
```bash
git clone https://github.com/hstarmans/esp32_hexastorm
git lfs fetch --all
``` 
After flashing the ESP32, see creating binary for the ESP32, connect to it using Thonny. Access the MicroPython shell via USB, to modify the config.json file 
and update the Wi-Fi password. In the micropython shell you can test the connection via
```python
import control.bootlib
control.bootlib.connect_wifi()
```
Reboot the ESP32 and you should be able to connect to the micropython webinterface by configuring
Thonny with the correct ip, port and password.
Program the FPGA.
```python
from hexastorm.controller import Host
hst = Host(micropython=True)
hst.flash_fpga("nameofbitfile.bit")
```
The bitfile is generated using the [https://github.com/hstarmans/hexastorm](hexastorm) library and
```shell
uv run python -m hexastorm.tests.build.test_build TestBuild.test_all
```

## Setting Up the Development Environment

Before building the MicroPython firmware, you need to install the necessary Python dependencies and link the `hexastorm` library.

1.  **Install `uv`:**
    `uv` is used as the dependency manager. Install it using `pipx`.
    ```bash
    sudo apt install pipx
    pipx ensurepath
    pipx install uv
    ```

2.  **Install Project Dependencies:**
    Navigate to the project directory and use `uv` to install the dependencies listed in `pyproject.toml`.
    ```bash
    uv sync
    ```

3.  **Link the `hexastorm` Library (Alternative to Submodules):**
    Instead of using Git submodules, a symbolic link can be created to the `hexastorm` library. 
    ```bash
    ln -s ~/CORRECTFOLDERONYOURSYSTEM/hexastorm/src/hexastorm/ THISPROJECT/src/hexastorm
    ```

4.  **Create the Jobs Directory:**
    Create the necessary directory for job files on the emulated SD card.
    ```bash
    mkdir -p THISPROJECT/src/root/sd/jobs/
    ```
## Creating a Binary for the ESP32 Microcontroller

The process of creating a MicroPython binary for the ESP32 involves using the Espressif IoT Development Framework (ESP-IDF). The following steps are based on the procedure described in the [MicroPython ESP32 port documentation](https://github.com/micropython/micropython/tree/master/ports/esp32).

1.  **Install and Activate ESP-IDF:**
    Clone the ESP-IDF repository (version v5.2.2 is specified) recursively and then run the installation script. You'll need to source the export script in each new terminal session. Note that Git might not be able to download ESP-IDF directly; ensure you have the necessary permissions and network configuration.
    ```bash
    git clone -b v5.2.2 --recursive https://github.com/espressif/esp-idf.git
    cd esp-idf
    ./install.sh       # (or install.bat on Windows)
    source export.sh   # (or export.bat on Windows)
    ```

2.  **Clone Additional Libraries:**
    Clone the `ulab` and `micropython` repositories. Ideally, `micropython-ulab` and `micropython` should reside in the same root folder.
    ```bash
    git clone https://github.com/v923z/micropython-ulab
    git clone https://github.com/micropython/micropython.git
    ```

3.  **Build `mpy-cross`:**
    Navigate to the `mpy-cross` directory within the `micropython` repository and build the cross-compiler.
    ```bash
    cd micropython
    cd mpy-cross
    make
    cd ..
    ```

4.  **Initialize ESP32 Submodules:**
    Go to the ESP32 port directory in the `micropython` repository and initialize the submodules.
    ```bash
    cd ports/esp32
    make submodules
    ```

5.  **Configure ESP32 Boot Mode and Serial Port:**
    The ESP32 needs to be in boot mode for flashing. This often involves setting the boot pin low and toggling the reset pin. The device will typically connect to a different serial port during this mode (e.g., `/dev/ttyS9` in Linux WSL, COM port in Windows Device Manager). Some boards with a serial controller can enter programmer mode automatically. The baud rate might also vary (e.g., 115200 for ESP32 devkit 1).

6.  **Configure Partition Scheme for ESP32S3:**
    For the ESP32S3 with 32MB of flash, the configuration for over-the-air (OTA) updates and the custom partition table needs to be set in the `sdkconfig.board` file within the `ESP32_GENERIC_S3` configuration. To function properly an ESP32 with octal RAM is required.
    ```
    CONFIG_BOOTLOADER_ROLLBACK_ENABLE=y
    CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y
    CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-32MiB-ota.csv
    CONFIG_ESPTOOLPY_OCT_FLASH=y
    ```

7.  **Copy `micropython.cmake`:**
    Copy the `micropython.cmake` file from this repository to the root directory containing `micropython-ulab`, and `micropython`.
    The cmake file ensure that micropython is build with ulab.

8.  **Build and Flash the Firmware:**
    Use the `make` command to build the firmware for the ESP32S3. The command includes options for the board, serial port, and frozen modules defined in the `manifest.py` file. The `erase` and `deploy` commands can be added to the end of this command to automatically erase the flash and deploy the new firmware. Adjust the `PORT` to match your ESP32's serial port (e.g., `/dev/ttyS16`).
    ```bash
    make BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=SPIRAM_OCT PORT=/dev/ttyS16 FROZEN_MANIFEST=/CORRECTROOTFOLDER/esp32_hexastorm/src/manifest.py USER_C_MODULES=../../../../micropython.cmake erase deploy
    ```

9.  **Configure Secrets (Optional):**
    After flashing, you might want to create a `secrets.json` file (by copying and modifying `config.json`) with your Wi-Fi ESSID and password and copy it to the ESP32 using a utility script. Ensure `uv` is installed for running this script. Adjust the device port as needed (e.g., `/dev/ttyS8`).
    ```bash
    uv run python utility.py --device /dev/ttyS8
    ```


## Testing

The project includes options for testing the webserver and hardware components in both Python and MicroPython environments.

### Testing Code in Python

1.  **Run the Webserver:**
    You can test the webserver directly from your Python environment. The default password is "hex".
    ```bash
    uv run python -m src.control.webapp
    ```

2.  **Run WebSocket Tests:**
    Specific tests for the WebSocket functionality can be executed using `pytest`. Refer to the code for any additional setup required for this test. The `-s` flag ensures print statements are displayed in the shell.
    ```bash
    uv run pytest -s --pyargs tests.test_webserver::test_websocket
    ```

### Testing Code in MicroPython

1.  **Navigate to the Source Directory:**
    ```bash
    cd src
    ```

2.  **Install MicroPython with `ulab` (Linux):**
    If you haven't already, install MicroPython on your Linux system with the `ulab` library. Follow the instructions on the [ulab GitHub repository](https://github.com/v923z/micropython-ulab).

3.  **Run Hardware Tests:**
    Execute the hardware-specific tests.
    ```bash
    micropython -m test.test_hardware
    ```

4.  **Run `hexastorm` Tests:**
    Run specific tests for the `hexastorm` library.
    ```bash
    micropython -m test.test_hexastorm LaserheadTest test_stable

## Tools
The best shell tool is `mpremote`. If you prefer a GUI use[Thonny](https://thonny.org/). Other tools like [Mpfshell](https://github.com/wendlers/mpfshell) and [rshell](https://github.com/dhylands/rshell) were tested but deemed less ideal.


## Tips

-   After running `make submodules`, it's necessary to execute `make clean` if you modify the `cmake` file.
-   You do not need to run `make submodules` every time you build the firmware.
-   rm the build directory prior to a new build