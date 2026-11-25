# ESP32-S3 Developer & Deployment Guide

This guide covers cloning with tracked submodules, dependency layout, building firmware cleanly with Docker or GitHub Actions CI (plus optional native ESP-IDF setup), flashing the FPGA bitstream, runtime architecture, and local testing.

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
   This also installs `esptool` into the virtual environment so host USB flashing works without needing a local ESP-IDF installation.

3. **Create the local jobs folder for CPython testing:**
   ```bash
   mkdir -p src/root/sd/jobs/
   ```

### Dependency Management Layout

External dependencies are split cleanly by role so no manual symlinks are needed:
* **C / Submodule dependencies (`deps/`):** Tracked via `.gitmodules` and pinned to tested commits/tags:
  * `deps/micropython-ulab` (`6.12`) — C vector/matrix library linked via [src/micropython.cmake](src/micropython.cmake).
  * `deps/micropython-camera-API` (`v0.6.2`) — MicroPython camera wrapper linked via [src/micropython.cmake](src/micropython.cmake) plus Python `acamera.py` frozen via [src/manifest.py](src/manifest.py).
  * `deps/esp32-camera` — ESP-IDF OV2640 sensor driver registered directly as component `esp32-camera` via `EXTRA_COMPONENT_DIRS` in [boards/HEXASTORM_S3/mpconfigboard.cmake](boards/HEXASTORM_S3/mpconfigboard.cmake).
  * `deps/hexastorm` — Hardware-agnostic FPGA gateware/host/calibration repo. During firmware builds, [docker/build_fpga.py](docker/build_fpga.py) synthesizes the iCE40UP5K `dispatcher` bitstream (`top.bin`) inside an isolated container venv (`/opt/fpga-venv` with `amaranth` + YoWASP tools) into `src/root/sd/fpga/fpga.bit` (cached by HDL source hash), while `src/manifest.py` only freezes lightweight pure-Python/`ulab` runtime modules (`ulabext.py`, `config.py`, `calibration/core.py`, `fpga_host/*`) so heavy PC/synthesis dependencies never enter MicroPython's Python build.
  * **Live dual-repo workflow:** `src/control/_paths.py` and `src/manifest.py` inspect `HEXASTORM_DIR` first, then `deps/hexastorm/src`, then sibling `../hexastorm/src`. Set `HEXASTORM_DIR=../hexastorm` during local co-development (even with `--docker`, which automatically bind-mounts it read-only), and bump the pinned submodule commit with `git submodule update --remote deps/hexastorm` once tested.
* **Vendored pure-Python MicroPython libs (`vendor/`):** `microdot`, `utemplate`, `ota`, `mrequests`, `winbond`, and `tmc` live in `vendor/` instead of `src/`. See [vendor/VERSIONS.md](vendor/VERSIONS.md) for upstream URLs, versions, licenses, and local patches (notably HTTPS TLS + redirect + streaming OTA patches in `vendor/mrequests/urequests.py`).

---

## Building & Flashing Firmware (Recommended: Docker)

The repository ships a self-contained [Dockerfile](Dockerfile) built on `espressif/idf:v5.5.2` with MicroPython `v1.28.0`, prebuilt `mpy-cross`, ESP32 submodules, `freezefs`, and `pyclean` baked into a cached image layer. Because `.dockerignore` excludes the workspace source tree from the Docker build context, edits to your Python/HTML/C files never invalidate the toolchain image cache.

1. **One-command build & flash (host USB):**
   ```bash
   # Builds Docker image automatically on first run, compiles build/HEXASTORM_S3/,
   # reboots the ESP32-S3 into bootloader, and flashes from host via esptool:
   uv run build --docker
   ```

2. **Other common Docker workflows:**
   ```bash
   # Compile binaries into build/HEXASTORM_S3/ + synthesize src/root/sd/fpga/fpga.bit
   uv run build --docker --build-only

   # Force FPGA resynthesis (bypassing the build/fpga/.fpga_hash cache)
   uv run build --docker --rebuild-fpga --build-only

   # Stage a prebuilt .bit file instead of running HDL synthesis
   uv run build --docker --fpga-bit ../hexastorm/build/top.bin

   # Compile locally on macOS/Linux in Docker and flash over SSH to a remote host
   uv run build --docker --remote hexastorm@pihome.home

   # Force rebuilding the Docker image (e.g. after editing Dockerfile)
   uv run build --docker --rebuild-image --build-only
   ```

3. **How host/container build caching works:**
   * `build.py --docker` mounts the repository at `/workspace` and persists IDF managed component downloads (`espressif__esp32-camera`, `espressif__mdns`, `espressif__tinyusb`) in a named Docker volume (`hexastorm-esp-cache:/root/.cache`).
   * Inside the container, [docker/build-firmware.sh](docker/build-firmware.sh) first runs [docker/build_fpga.py](docker/build_fpga.py) inside `/opt/fpga-venv` to build (or reuse cached) `src/root/sd/fpga/fpga.bit`, then runs `make` using the out-of-tree `boards/HEXASTORM_S3/` board definition and fixes file ownership on Linux via `HOST_UID`/`HOST_GID`.
   * [src/manifest.py](src/manifest.py) verifies `src/root/sd/fpga/fpga.bit` is present before running `freezefs`, and freezes `FIRMWARE_VERSION` and `FPGA_VERSION` into `src/control/build_info.py`. It calls `python_module_cmd(...)` so `freezefs` and `pyclean` execute cleanly in both Docker and native environments.

### Out-of-Tree Board Definition (`boards/HEXASTORM_S3/`)

No manual edits to upstream MicroPython are ever needed. `boards/HEXASTORM_S3/` provides:
* `mpconfigboard.cmake` — sets `IDF_TARGET esp32s3`, selects 240 MHz + Octal SPIRAM defaults, configures `sdkconfig.board.in`, and appends `deps/esp32-camera` to `EXTRA_COMPONENT_DIRS`.
* `partitions-32MiB-ota.csv` & `sdkconfig.board.in` — 32 MiB Octal Flash with dual `ota_0`/`ota_1` partitions (`0x300000` each) and app rollback enabled.
* `mpconfigboard.h` — sets `MICROPY_HW_I2C0_SCL (17)`, `MICROPY_HW_I2C0_SDA (18)`, and OV2640 pins matching `PlatformConfig.esp32_cfg` in `hexastorm/config.py` (`SIOD = 18` SDA, `SIOC = 17` SCL, `XCLK = 10 MHz`, verified by `test_project_layout.py`).

### GitHub Actions CI & Automated OTA Releases (`.github/workflows/ci.yml`)

Every push and pull request runs:
* **`test` job:** `uv sync`, `uv run pytest`, `uv run ruff check src docker`, and `uv run pyrefly check`.
* **`firmware` job (`needs: test`):** Runs `docker/run-mpy-tests.sh`, builds `HEXASTORM_S3` and `src/root/sd/fpga/fpga.bit` inside the `Dockerfile` container, and uploads `micropython.bin`, `firmware.bin`, `bootloader.bin`, `partition-table.bin`, `ota_data_initial.bin`, and `fpga.bit` as workflow artifacts.
* **Tagged CalVer OTA releases (`git tag -a vYYYY.MM.N -m "Release vYYYY.MM.N" && git push origin vYYYY.MM.N`, e.g. `v2026.11.1`):** Automatically publishes a GitHub Release with auto-generated release notes and `micropython.bin` attached so ESP32-S3 units can update directly via the web UI (`bootlib.update_firmware()`). Tags containing `-` (such as `v2026.11.1-rc1`) are published as pre-releases.

---

## Optional: Native Toolchain Build (Without Docker)

If you prefer building natively outside Docker (ESP-IDF `v5.5.2` + MicroPython `v1.28.0`):

1. **Install ESP-IDF `v5.5.2`:**
   ```bash
   git clone -b v5.5.2 --recursive https://github.com/espressif/esp-idf.git
   cd esp-idf
   ./install.sh esp32s3
   source export.sh
   ```

2. **Clone `micropython` (`v1.28.0`), build `mpy-cross`, and fetch ESP32 submodules:**
   ```bash
   git clone --depth 1 --branch v1.28.0 https://github.com/micropython/micropython.git
   cd micropython
   git submodule update --init --recursive --depth 1
   make -C mpy-cross -j
   make -C ports/esp32 BOARD=ESP32_GENERIC_S3 submodules
   ```

3. **Build & flash via `uv run build`:**
   Point `MICROPYTHON_DIR` and `IDF_PATH` (or `--micropython-dir` / `--idf-path`) at your native checkouts; defaults fall back to `~/Documents/Python/{micropython,esp-idf}`:
   ```bash
   export MICROPYTHON_DIR=/path/to/micropython
   export IDF_PATH=/path/to/esp-idf

   uv run build
   uv run build --port /dev/ttyACM0
   uv run build --remote hexastorm@pihome.home
   ```

---

## First-Time Device Bring-Up & FPGA Flashing

After flashing MicroPython, connect over USB using `mpremote` (recommended) or [Thonny](https://thonny.org/).

1. **Test or configure Wi-Fi from the MicroPython REPL:**
   ```python
   import asyncio
   from control import bootlib

   asyncio.run(bootlib.connect_wifi())
   ```
2. **FPGA Bitstream Workflow (Bundled vs. Rapid HDL Iteration):**
   Every firmware build bundles `src/root/sd/fpga/fpga.bit` via `freezefs`, so `boot.py` → `deploy_assets()` extracts `/sd/fpga/fpga.bit` automatically on first boot or after an ESP32/OTA update, and `ESP32Host.reset()` flashes the iCE40UP5K over SPI at startup.
   For fast gateware iteration in the sibling [Hexastorm](https://github.com/hstarmans/hexastorm) repository without rebuilding the ESP32 firmware:
   ```bash
   uv run build
   ```
   This uploads `:sd/fpga/fpga.bit` directly via `mpremote` and calls `flash_fpga('sd/fpga/fpga.bit')`. Because `deploy_assets()` skips extraction across normal reboots when `BUILD_ID` is unchanged, your manually uploaded bitstream stays active until the next firmware flash/OTA.

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

Run the fast in-process `pytest` suite (`microdot.test_client.TestClient`, mock `FakeScanhead` calibration, `.pat` packet streaming, REST endpoint guards, frontend template/JS route contract checks, Docker/CI/layout checks, and `HEXASTORM_S3` pin alignment) and linter:
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
