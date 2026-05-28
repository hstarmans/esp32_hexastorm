# ESP32 Webserver Deployment
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
    `uv` is used as the dependency manager.
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
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
    Clone the ESP-IDF repository (version v5.5.2 is specified) recursively and run the installation script. You'll need to source the export script in each new terminal session. Note that Git might not be able to download ESP-IDF directly; ensure you have the necessary permissions and network configuration.
    ```bash
    git checkout v5.5.2 --recursive https://github.com/espressif/esp-idf.git
    cd esp-idf
    ./install.sh  esp32s3  # (or install.bat on Windows)
    source export.sh       # (or export.bat on Windows)
    ```

2.  **Clone Additional Libraries:**
    Clone the `ulab` and `micropython` repositories. Ideally, `micropython-ulab` and `micropython` should reside in the same root folder.
    ```bash
    git clone 6.11.0 --depth 1 https://github.com/v923z/micropython-ulab.git
    git clone https://github.com/micropython/micropython.git
    ```

3.  **Build `mpy-cross`:**
    Navigate to the `mpy-cross` directory within the `micropython` repository and build the cross-compiler.
    ```bash
    cd micropython
    git checkout -f v1.28.0 # latest tested version
    git submodule update --init --recursive
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
    For the ESP32S3 with 32MB of flash, the configuration for over-the-air (OTA) updates and the custom partition table needs to be set in the `sdkconfig.board` file within the `ESP32_GENERIC_S3` configuration. To function properly, an ESP32 with octal RAM is required.
    
    `../ports/esp32/boards/ESP32_GENERIC_S3/sdkconfig.board`
    ```text
    CONFIG_ESPTOOLPY_FLASHMODE_OPI=y
    CONFIG_ESPTOOLPY_FLASHFREQ_80M=y
    CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y
    CONFIG_ESPTOOLPY_OCT_FLASH=y
    CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
    CONFIG_PARTITION_TABLE_CUSTOM=y
    CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-32MiB-ota.csv"
    ```

    The following file is needed for the partition. The VFS partition is appended via autoscaling.
    
    `../ports/esp32/partitions-32MiB-ota.csv`
    ```csv
    # Partition table for MicroPython with OTA support using 32MB flash
    # Notes: the offset of the partition table itself is set in
    # $IDF_PATH/components/partition_table/Kconfig.projbuild.
    # Name,   Type, SubType, Offset,   Size,     Flags
    nvs,      data, nvs,     0x9000,   0x4000,
    otadata,  data, ota,     0xd000,   0x2000,
    phy_init, data, phy,     0xf000,   0x1000,
    ota_0,    app,  ota_0,   0x10000,  0x300000,
    ota_1,    app,  ota_1,   0x310000, 0x300000,
    ```

    **Configure mpconfigboard.cmake:**
    To ensure the MicroPython build system utilizes the custom partition table, add the following to:
    `../ports/esp32/boards/ESP32_GENERIC_S3/mpconfigboard.cmake`
    ```cmake
    set(PARTITION_CSV "partitions-32MiB-ota.csv")
    ```

    **Adapt idf_component.yml:**
    In `../ports/esp32/main/idf_component.yml`, use:
    ```yaml
    dependencies:
        espressif/mdns: "~1.1.0"
        espressif/tinyusb:
            rules:
            - if: "target in [esp32s2, esp32s3, esp32p4]"
            # Temporary workaround for [https://github.com/hathach/tinyusb/issues/3154](https://github.com/hathach/tinyusb/issues/3154)
            # Can be removed once fix is released in espressif/tinyusb
            git: [https://github.com/micropython/tinyusb-espressif.git](https://github.com/micropython/tinyusb-espressif.git)
            version: cherrypick/dwc2_zlp_fix
        espressif/esp32-camera:
            git: [https://github.com/cnadler86/esp32-camera.git](https://github.com/cnadler86/esp32-camera.git)
        espressif/esp_hosted:
            rules:
            - if: "target == esp32p4"
            version: "2.7.0"
        espressif/esp_wifi_remote:
            rules:
            - if: "target == esp32p4"
            version: "0.15.2"
        espressif/lan867x:
            version: "~1.0.0"
            rules:
            - if: "target == esp32"
        idf:
            version: ">=5.3.0"
    ```

    **Configure mpconfigboard.h:**
    In `../ports/esp32/boards/ESP32_GENERIC_S3/mpconfigboard.h`
    ```c
    #ifndef MICROPY_HW_BOARD_NAME
    // Can be set by mpconfigboard.cmake.
    #define MICROPY_HW_BOARD_NAME               "Hexastorm ESP32S3"
    #endif
    #define MICROPY_HW_MCU_NAME                 "ESP32S3"

    // Enable UART REPL for modules that have an external USB-UART and don't use native USB.
    #define MICROPY_HW_ENABLE_UART_REPL         (1)

    #define MICROPY_HW_I2C0_SCL                 (17)
    #define MICROPY_HW_I2C0_SDA                 (18)

    #define MICROPY_CAMERA_PIN_D0       (12)
    #define MICROPY_CAMERA_PIN_D1       (14)
    #define MICROPY_CAMERA_PIN_D2       (21)
    #define MICROPY_CAMERA_PIN_D3       (13)
    #define MICROPY_CAMERA_PIN_D4       (11)
    #define MICROPY_CAMERA_PIN_D5       (10)
    #define MICROPY_CAMERA_PIN_D6       (9)
    #define MICROPY_CAMERA_PIN_D7       (8)
    #define MICROPY_CAMERA_PIN_PCLK     (38)
    #define MICROPY_CAMERA_PIN_VSYNC    (41)
    #define MICROPY_CAMERA_PIN_HREF     (40)
    #define MICROPY_CAMERA_PIN_XCLK     (39)
    #define MICROPY_CAMERA_PIN_PWDN     (-1)
    #define MICROPY_CAMERA_PIN_RESET    (-1)
    #define MICROPY_CAMERA_PIN_SIOD     (17)        // SDA
    #define MICROPY_CAMERA_PIN_SIOC     (18)        // SCL
    #define MICROPY_CAMERA_XCLK_FREQ    (20000000)  // Frequencies are normally either 10 MHz or 20 MHz
    #define MICROPY_CAMERA_FB_COUNT     (2)         // The value is between 1 (slow) and 2 (fast, but more load on CPU and more ram usage)
    #define MICROPY_CAMERA_JPEG_QUALITY (85)        // Quality of JPEG output in percent. Higher means higher quality.
    #define MICROPY_CAMERA_GRAB_MODE    (1)         // 0=WHEN_EMPTY (might have old data, but less resources), 1=LATEST (best, but more resources)
    ```

7.  **Copy `micropython.cmake`:**
    Copy the `micropython.cmake` file from this repository to the root directory containing `micropython-ulab`, and `micropython`.
    The cmake file ensures that MicroPython is built with ulab.

8.  **Build and Flash the Firmware:**
    Use the `make` command to build the firmware for the ESP32S3. The command includes options for the board, serial port, and frozen modules defined in the `manifest.py` file. The `erase` and `deploy` commands can be added to the end of this command to automatically erase the flash and deploy the new firmware. Adjust the `PORT` to match your ESP32's serial port (e.g., `/dev/ttyS16`).
    ```bash
    make BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=SPIRAM_OCT PORT=/dev/ttyACM0 FROZEN_MANIFEST=/CORRECTROOTFOLDER/esp32_hexastorm/src/manifest.py USER_C_MODULES=../../../../micropython.cmake erase deploy
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

## Tools
The best shell tool is `mpremote`. If you prefer a GUI use[Thonny](https://thonny.org/). Other tools like [Mpfshell](https://github.com/wendlers/mpfshell) and [rshell](https://github.com/dhylands/rshell) were tested but deemed less ideal.
