# ESP32-S3 Developer & Deployment Guide

This guide covers setting up the development environment, building custom MicroPython firmware for the ESP32-S3 (with `ulab`, `micropython-camera-API`, OTA partitions, and frozen `hexastorm` modules), flashing the FPGA bitstream, understanding runtime architecture, and testing locally.

---

## Setting Up the Development Environment

1. **Clone the repository (including LFS assets):**
   ```bash
   git clone https://github.com/hstarmans/esp32_hexastorm
   cd esp32_hexastorm
   git lfs fetch --all
   ```

2. **Install `uv` and project dependencies:**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv sync
   ```

3. **Symlink sibling libraries into `src/`:**
   `src/manifest.py` expects `hexastorm` (and for firmware builds, `micropython-camera-API`) inside `src/`:
   ```bash
   ln -s ~/Documents/projects/hexastorm/src/hexastorm ./src/hexastorm
   ln -s ../../micropython-camera-API ./src/micropython-camera-API
   ln -s ../../micropython-ulab ./src/micropython-ulab
   ```
   Tested versions: [micropython-ulab](https://github.com/v923z/micropython-ulab) `6.12.0` and [micropython-camera-API](https://github.com/cnadler86/micropython-camera-API) `v0.6.2`.

4. **Create the local jobs folder for CPython testing:**
   ```bash
   mkdir -p src/root/sd/jobs/
   ```

---

## Building the ESP32-S3 MicroPython Firmware

The firmware build relies on ESP-IDF `v5.5.2` and MicroPython (`v1.28.0`) with custom 32 MiB OTA partitions and Octal PSRAM enabled.

1. **Install and activate ESP-IDF (`v5.5.2`):**
   ```bash
   git clone -b v5.5.2 --recursive https://github.com/espressif/esp-idf.git
   cd esp-idf
   ./install.sh esp32s3
   source export.sh
   ```

2. **Clone `micropython-ulab` and `micropython` in the same parent folder:**
   ```bash
   git clone --branch 6.12.0 --depth 1 https://github.com/v923z/micropython-ulab.git
   git clone https://github.com/micropython/micropython.git
   ```

3. **Build `mpy-cross` and initialize ESP32 submodules:**
   ```bash
   cd micropython
   git checkout -f v1.28.0
   git submodule update --init --recursive
   cd mpy-cross
   make
   cd ../ports/esp32
   make submodules
   ```

4. **Configure the 32 MiB OTA + Octal SPIRAM board profile (`ESP32_GENERIC_S3`):**

   `ports/esp32/boards/ESP32_GENERIC_S3/sdkconfig.board`:
   ```text
   CONFIG_ESPTOOLPY_FLASHMODE_OPI=y
   CONFIG_ESPTOOLPY_FLASHFREQ_80M=y
   CONFIG_ESPTOOLPY_FLASHSIZE_32MB=y
   CONFIG_ESPTOOLPY_OCT_FLASH=y
   CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
   CONFIG_PARTITION_TABLE_CUSTOM=y
   CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-32MiB-ota.csv"
   ```

   `ports/esp32/partitions-32MiB-ota.csv` (the MicroPython VFS partition is appended automatically):
   ```csv
   # Partition table for MicroPython with OTA support using 32MB flash
   # Offset of partition table is set in $IDF_PATH/components/partition_table/Kconfig.projbuild
   # Name,   Type, SubType, Offset,   Size,     Flags
   nvs,      data, nvs,     0x9000,   0x4000,
   otadata,  data, ota,     0xd000,   0x2000,
   phy_init, data, phy,     0xf000,   0x1000,
   ota_0,    app,  ota_0,   0x10000,  0x300000,
   ota_1,    app,  ota_1,   0x310000, 0x300000,
   ```

   `ports/esp32/boards/ESP32_GENERIC_S3/mpconfigboard.cmake`:
   ```cmake
   set(PARTITION_CSV "partitions-32MiB-ota.csv")
   ```

5. **Adapt `ports/esp32/main/idf_component.yml`:**
   ```yaml
   dependencies:
       espressif/mdns: "~1.1.0"
       espressif/tinyusb:
           rules:
           - if: "target in [esp32s2, esp32s3, esp32p4]"
           # Workaround for https://github.com/hathach/tinyusb/issues/3154
           git: https://github.com/micropython/tinyusb-espressif.git
           version: cherrypick/dwc2_zlp_fix
       espressif/esp32-camera:
           git: https://github.com/cnadler86/esp32-camera.git
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

6. **Configure `ports/esp32/boards/ESP32_GENERIC_S3/mpconfigboard.h`:**
   Note that I2C and OV2640 pin assignments match `PlatformConfig.esp32_cfg` in `hexastorm/config.py` (`SDA = 18`, `SCL = 17`, `XCLK = 10 MHz`), and `src/control/camera.py` also passes those runtime settings explicitly into `camera.Camera(...)`:
   ```c
   #ifndef MICROPY_HW_BOARD_NAME
   #define MICROPY_HW_BOARD_NAME               "Hexastorm ESP32S3"
   #endif
   #define MICROPY_HW_MCU_NAME                 "ESP32S3"

   #define MICROPY_HW_ENABLE_UART_REPL         (1)

   #define MICROPY_HW_I2C0_SCL                 (17)
   #define MICROPY_HW_I2C0_SDA                 (18)

   #define MICROPY_CAMERA_PIN_D0               (12)
   #define MICROPY_CAMERA_PIN_D1               (14)
   #define MICROPY_CAMERA_PIN_D2               (21)
   #define MICROPY_CAMERA_PIN_D3               (13)
   #define MICROPY_CAMERA_PIN_D4               (11)
   #define MICROPY_CAMERA_PIN_D5               (10)
   #define MICROPY_CAMERA_PIN_D6               (9)
   #define MICROPY_CAMERA_PIN_D7               (8)
   #define MICROPY_CAMERA_PIN_PCLK             (38)
   #define MICROPY_CAMERA_PIN_VSYNC            (41)
   #define MICROPY_CAMERA_PIN_HREF             (40)
   #define MICROPY_CAMERA_PIN_XCLK             (39)
   #define MICROPY_CAMERA_PIN_PWDN             (-1)
   #define MICROPY_CAMERA_PIN_RESET            (42)
   #define MICROPY_CAMERA_PIN_SIOD             (18)        // SDA
   #define MICROPY_CAMERA_PIN_SIOC             (17)        // SCL
   #define MICROPY_CAMERA_XCLK_FREQ            (10000000)  // 10 MHz (matches PlatformConfig.esp32_cfg)
   #define MICROPY_CAMERA_FB_COUNT             (1)
   #define MICROPY_CAMERA_JPEG_QUALITY         (85)
   #define MICROPY_CAMERA_GRAB_MODE            (1)         // 0=WHEN_EMPTY, 1=LATEST
   ```

7. **Copy `src/micropython.cmake`:**
   Copy `src/micropython.cmake` into the parent directory containing `micropython-ulab` and `micropython` so `USER_C_MODULES` builds `ulab` into MicroPython.

---

## Automated Build, Flash & Frozen Manifest

The repository provides `uv run build` (`src/build.py`) to automate board detection and flashing:

```bash
# Auto-detect ESP32-S3 over USB, reboot from MicroPython to bootloader, build & flash
uv run build

# Specify an explicit serial port
uv run build --port /dev/ttyACM0

# Build locally and flash to a board attached to a remote SSH host
uv run build --remote hexastorm@pihome.home
```

To invoke `make` manually inside `ports/esp32`:
```bash
make -j$(nproc 2>/dev/null || sysctl -n hw.ncpu) \
    BOARD=ESP32_GENERIC_S3 BOARD_VARIANT=SPIRAM_OCT \
    FROZEN_MANIFEST=/path/to/esp32_hexastorm/src/manifest.py \
    USER_C_MODULES=../../../../micropython.cmake erase deploy
```

### What `src/manifest.py` Packages

Whenever `manifest.py` runs, it:
1. Deletes stale compiled `root/templates/*.py` files and packs `src/root/` via `freezefs` into `src/control/frozen_root.py` along with a timestamped `BUILD_ID` (`src/control/build_info.py`).
2. Freezes `control`, `microdot`, `utemplate`, `ota`, `mrequests`, `winbond`, `tmc`, `acamera.py`, and explicit `hexastorm` modules (`__init__.py`, `ulabext.py`, `config.py`, `calibration/__init__.py`, `calibration/core.py`, `fpga_host/*`, `tests/test_mpy.py`).

---

## First-Time Device Bring-Up & FPGA Flashing

After flashing MicroPython, connect over USB using `mpremote` (recommended) or [Thonny](https://thonny.org/).

1. **Test or configure Wi-Fi from the MicroPython REPL:**
   ```python
   import asyncio
   from control import bootlib

   asyncio.run(bootlib.connect_wifi())
   ```
2. **Generate and flash the iCE40UP5K FPGA bitstream:**
   Generate the `.bit` file in the sibling [Hexastorm](https://github.com/hstarmans/hexastorm) repository with:
   ```bash
   uv run build
   ```
   Copy the `.bit` file onto the ESP32 and flash the FPGA from the ESP32 REPL:
   ```python
   import tools

   tools.lh.flash_fpga("nameofbitfile.bit")
   ```

---

## Runtime Architecture

### Boot Sequence & Crash Recovery
1. `src/boot.py` imports `control.bootlib` → `control.constants`. On ESP32, `constants.deploy_assets()` extracts `frozen_root` whenever `BUILD_ID` changes, safely backing up and recursively merging existing `config.json` user settings via `merge_configs()`, and invalidates stale compiled templates.
2. `bootlib.check_crash_loop_rtc()` increments a boot counter in RTC RAM and raises a safe-mode error after `3` consecutive failed boots so USB/REPL recovery stays reachable.
3. `src/main.py` starts `network_manager()` (STA Wi-Fi with AP fallback + NTP + RGB status LEDs), optional WebREPL, the Microdot webserver on port `5000`, and a background `_mark_boot_stable()` task that waits 5 seconds before clearing the RTC crash counter via `bootlib.mark_boot_successfull()`.

### Persistence (`SafeNVS` & `config.json`)
* `SafeNVS` (`src/control/constants.py`) stores `mpos_{x,y,z}` and `woff_{x,y,z}` in ESP32 NVS as 32-bit micrometer integers (`mm × 1000`), falling back to `src/root/nvs_mock.json` on PC.
* `CONFIG` is loaded from `config.json` (`src/root/mock_config.json` on PC) merged over factory defaults. Calling `update_config()` sanitizes types (`int`/`float`/`bool`), writes JSON back to disk, and synchronizes OV2640 manual exposure.

### `.pat` Streaming (`file_exposures` vs `repeat_factor`)
`BaseLaserhead.print_loop_prep()` parses optional filename suffixes `<name>_e<N>_<cor|nocor>.pat`:
* Uncorrected single-exposure files (`*_e1_nocor.pat`) allow GUI `exposureperline` repetition (`_repeat_factor = exposures_gui`, `_file_exposures = 1`) and recompute the 24 MHz `step_half_period` via `ulabext.calc_step_half_period(1.0 / repeat_factor, scanline_ticks)` plus direction for each line.
* Pre-sliced or optically corrected files (`*_e4_cor.pat`) keep `_file_exposures = N` and `_repeat_factor = 1` (`0.25` step per file line for `e4`) and stream directly in chunks without re-repeating packets.

### Webserver REST & SSE Endpoints (`src/control/webapp.py`)

Except `/` (login/UI) and `/static/<path>`, all endpoints require a valid session cookie set via `POST /` (default password `hex`). Unauthenticated API calls return `401`; page navigation redirects to `/`.

| Group | Method & Path | Purpose |
| :--- | :--- | :--- |
| **Auth & UI** | `GET /`, `POST /`, `GET /logout` | Login form, session auth, rendered `home.html`, logout redirect |
| **Live State** | `GET /state` | Server-Sent Events stream pushing `devicestate.data` changes |
| **Motion** | `POST /gotopoint`, `POST /home`, `POST /setworkspacezero` | Coordinated moves with soft-limit checks, homing, WPOS zeroing |
| **Safety & Reset** | `POST /control/abort`, `POST /control/reset`, `POST /clearerror`, `POST /reset` | E-stop lock/unlock, clear UI error modal, delayed MCU reset |
| **Peripherals** | `POST /control/laser`, `/prism`, `/spindle`, `/fan`, `/diodetest`, `/save_facet_means` | Toggle laser/prism, PWM control, run photodiode jitter test, save baseline `facetmeans` |
| **Files & Print** | `POST /upload`, `POST /deletefile`, `POST /print/control` | Stream `.pat`/G-code upload (quota + name sanitized), delete job, `start`/`pause`/`stop` job |
| **Settings & OTA** | `GET/POST /api/settings`, `POST /api/settings/reset`, `GET /api/system/update/check`, `POST /api/system/update/apply` | Read/save config, factory reset, GitHub release check & direct-to-flash OTA |
| **Camera & Calibration** | `GET /api/camera/snapshot`, `POST /api/camera/settings`, `/api/calibration/{save_camera_pos,move_to_camera,scan_visibility,show_bounds,calibrate,pattern_error,update_facets}` | OV2640 JPEG feed, exposure settings, target MPOS, FOV scan, dot preview, 2D calibration & verification |

---

## Testing

### Local Webserver & Automated Suite (CPython)

Run the mock webserver locally (port `5000`, password `hex`) using `FakeScanhead` as the virtual camera:
```bash
uv run webapp
```

Run the fast in-process `pytest` suite (`microdot.test_client.TestClient`, mock `FakeScanhead` calibration, `.pat` packet streaming, REST endpoint guards, and frontend template/JS route contract checks) and linter:
```bash
uv run pytest
uv run ruff check src
```

Note that `pyproject.toml` limits host `pytest` discovery to `test_cameracontroller.py` and `test_webapp_endpoints.py` because the remaining scripts in `src/tests/` target on-device ESP32 hardware.

### On-Device Hardware Tests (MicroPython)

Connect via `mpremote` and invoke FPGA/hardware tests registered in `src/tools.py`:
```python
import tools

tools.test("StaticTest", "test_memfull")
```
