# ESP32-S3 Developer & Deployment Guide

This guide covers cloning with tracked submodules, dependency layout, building MicroPython firmware using the out-of-tree `boards/HEXASTORM_S3/` board definition, flashing the FPGA bitstream, runtime architecture, and local testing.

---

## Setting Up the Development Environment

1. **Clone with submodules and LFS assets:**
   ```bash
   git clone --recursive https://github.com/hstarmans/esp32_hexastorm
   cd esp32_hexastorm
   git lfs fetch --all
   ```
   If you cloned without `--recursive`, initialize submodules afterwards:
   ```bash
   git submodule update --init --recursive
   ```

2. **Install `uv` and project dependencies:**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv sync
   ```

3. **Create the local jobs folder for CPython testing:**
   ```bash
   mkdir -p src/root/sd/jobs/
   ```

### Dependency Management Layout

External dependencies are split cleanly by role so no manual symlinks are needed:
* **C / Submodule dependencies (`deps/`):** Tracked via `.gitmodules` and pinned to tested commits/tags:
  * `deps/micropython-ulab` (`6.12`) — C vector/matrix library linked via [src/micropython.cmake](src/micropython.cmake).
  * `deps/micropython-camera-API` (`v0.6.2`) — C OV2640 camera driver linked via [src/micropython.cmake](src/micropython.cmake) plus Python `acamera.py` frozen via [src/manifest.py](src/manifest.py).
  * `deps/hexastorm` — Hardware-agnostic FPGA gateware/host/calibration repo. Only lightweight pure-Python/`ulab` modules (`ulabext.py`, `config.py`, `calibration/core.py`, `fpga_host/*`) are frozen into MicroPython, so heavy PC dependencies (`amaranth`, `yowasp-yosys`, `matplotlib`) are never pulled into `esp32_hexastorm`.
  * **Live dual-repo workflow:** `src/control/_paths.py` and `src/manifest.py` inspect `HEXASTORM_DIR` first, then `deps/hexastorm/src`, then sibling `../hexastorm/src`. Set `HEXASTORM_DIR=../hexastorm` during local co-development, and bump the pinned submodule commit with `git submodule update --remote deps/hexastorm` once tested.
* **Vendored pure-Python MicroPython libs (`vendor/`):** `microdot`, `utemplate`, `ota`, `mrequests`, `winbond`, and `tmc` live in `vendor/` instead of `src/`. See [vendor/VERSIONS.md](vendor/VERSIONS.md) for upstream URLs, versions, licenses, and local patches (notably HTTPS TLS + redirect + streaming OTA patches in `vendor/mrequests/urequests.py`).

---

## Building the ESP32-S3 MicroPython Firmware

Firmware builds use ESP-IDF `v5.5.2` and MicroPython `v1.28.0`. All board-specific configuration, 32 MiB OTA partitions, OV2640 pin macros, and `esp32-camera` IDF component dependencies live in `boards/HEXASTORM_S3/`, so the `micropython` checkout stays completely unmodified.

1. **Install and activate ESP-IDF (`v5.5.2`):**
   ```bash
   git clone -b v5.5.2 --recursive https://github.com/espressif/esp-idf.git
   cd esp-idf
   ./install.sh esp32s3
   source export.sh
   ```

2. **Clone `micropython` (`v1.28.0`) and build `mpy-cross`:**
   ```bash
   git clone https://github.com/micropython/micropython.git
   cd micropython
   git checkout -f v1.28.0
   git submodule update --init --recursive
   cd mpy-cross
   make
   cd ../ports/esp32
   make submodules
   ```

3. **Out-of-tree board definition (`boards/HEXASTORM_S3/`):**
   No manual edits to `ports/esp32/boards/ESP32_GENERIC_S3/` or `ports/esp32/main/idf_component.yml` are required. `boards/HEXASTORM_S3/` provides:
   * `mpconfigboard.cmake` — sets `IDF_TARGET esp32s3`, selects 240 MHz + Octal SPIRAM defaults, configures `sdkconfig.board.in`, and registers `components/hexastorm_camera_dep`.
   * `components/hexastorm_camera_dep/idf_component.yml` — pulls `espressif/esp32-camera` from `https://github.com/cnadler86/esp32-camera.git`.
   * `partitions-32MiB-ota.csv` & `sdkconfig.board.in` — 32 MiB Octal Flash with dual `ota_0`/`ota_1` partitions (`0x300000` each) and app rollback enabled.
   * `mpconfigboard.h` — sets `MICROPY_HW_I2C0_SCL (17)`, `MICROPY_HW_I2C0_SDA (18)`, and OV2640 pins matching `PlatformConfig.esp32_cfg` in `hexastorm/config.py` (`SIOD = 18` SDA, `SIOC = 17` SCL, `XCLK = 10 MHz`, verified by `test_project_layout.py`).

---

## Automated Build, Flash & Frozen Manifest

`src/build.py` (`uv run build`) auto-detects the ESP32-S3 (USB VID `0x303A`), reboots MicroPython into bootloader mode, compiles using `BOARD_DIR=boards/HEXASTORM_S3`, and flashes via `esptool`.

Configure toolchains via environment variables (or CLI flags) instead of hardcoded paths; defaults fall back to `~/Documents/Python/{micropython,esp-idf}`:

```bash
export MICROPYTHON_DIR=/path/to/micropython
export IDF_PATH=/path/to/esp-idf

uv run build
uv run build --port /dev/ttyACM0
uv run build --remote hexastorm@pihome.home
```

To invoke `make` manually inside `ports/esp32`:
```bash
make -j$(nproc 2>/dev/null || sysctl -n hw.ncpu) \
    BOARD=HEXASTORM_S3 \
    BOARD_DIR=/path/to/esp32_hexastorm/boards/HEXASTORM_S3 \
    FROZEN_MANIFEST=/path/to/esp32_hexastorm/src/manifest.py \
    USER_C_MODULES=/path/to/esp32_hexastorm/src/micropython.cmake \
    erase deploy
```

### What `src/manifest.py` Packages

Whenever `manifest.py` runs, it:
1. Cleans stale compiled `root/templates/*.py` files and packs `src/root/` via `freezefs` into `src/control/frozen_root.py` along with a timestamped `BUILD_ID` (`src/control/build_info.py`).
2. Freezes `control/` from `src/`.
3. Freezes vendored packages (`microdot`, `utemplate`, `ota`, `mrequests`, `winbond`, `tmc`) from `vendor/`.
4. Freezes explicit `hexastorm` modules (`__init__.py`, `ulabext.py`, `config.py`, `calibration/__init__.py`, `calibration/core.py`, `fpga_host/*`, `tests/test_mpy.py`) via `resolve_hexastorm_src()` and `acamera.py` from `deps/micropython-camera-API/src`.

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

Run the fast in-process `pytest` suite (`microdot.test_client.TestClient`, mock `FakeScanhead` calibration, `.pat` packet streaming, REST endpoint guards, frontend template/JS route contract checks, and board/submodule layout verification) and linter:
```bash
uv run pytest
uv run ruff check src
```

Note that `pyproject.toml` restricts host `pytest` discovery to `test_cameracontroller.py`, `test_webapp_endpoints.py`, and `test_project_layout.py` because the remaining scripts in `src/tests/` target on-device ESP32 hardware.

### On-Device Hardware Tests (MicroPython)

Connect via `mpremote` and invoke FPGA/hardware tests registered in `src/tools.py`:
```python
import tools

tools.test("StaticTest", "test_memfull")
```
