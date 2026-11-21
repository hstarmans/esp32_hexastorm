# ESP32-S3 Webserver & Controller for Hexastorm

This project provides an asynchronous Microdot webserver and hardware controller running on an **ESP32-S3** microcontroller (MicroPython), enabling remote operation of a dual-mode PCB fabrication workstation (**UV Laser Lithography** and **CNC Milling**).

The workstation's real-time timing is driven by a Lattice iCE40UP5K FPGA programmed with the [Hexastorm](https://github.com/hstarmans/hexastorm) Amaranth HDL toolchain, connected to the ESP32-S3 over 5 MHz SPI (`src/hexastorm` symlink) and sharing the pure Python / `ulab` optical calibration core (`hexastorm.calibration.core`).

---

## Architecture Overview

```text
Browser UI (Alpine.js + Bootstrap + SSE /state)
   │  HTTP + Server-Sent Events (port 5000, default password: hex)
   ▼
ESP32-S3 MicroPython (Microdot webapp.py + BaseLaserhead / Laserhead)
   ├──▶ 5 MHz SPI (cmd + 64-bit word) ──▶ Lattice iCE40UP5K FPGA
   │       • 24 MHz event-driven laser scanline & PDM modulation
   │       • 1 MHz discrete forward-difference X/Y/Z stepper moves
   ├──▶ UART / I2C / PWM ──▶ TMC2209 steppers, digipot, prism, spindle, fan
   └──▶ Parallel DVP + I2C ──▶ OV2640 camera + on-board ulab 2D calibration
```

* **Coordinate Persistence & Crash Protection:** Machine (`mpos`) and workspace offset (`woff`) coordinates are persisted in ESP32 NVS in micrometers via `SafeNVS` (`nvs_mock.json` on PC). Boot crashes are tracked in RTC memory (`bootlib.check_crash_loop_rtc()`), entering safe mode after repeated failures and clearing the counter after 5 seconds of stable uptime (`_mark_boot_stable()`).
* **Timing Split on FPGA:** Laser exposures run at `24 MHz` (photodiode-locked scanline events + Sigma-Delta PDM), while coordinated homing, positioning, and CNC G-code moves run on the `1 MHz` forward-difference stepper engine.

---

## Features

* **Coordinated Multi-Axis Motion:** 3D linear interpolation (`G0`/`G1`) across X, Y, and Z axes with per-axis soft limits and E-stop locking.
* **Smart Job Launcher (`.pat` & G-code):**
  * **Laser Mode (`.pat`):** Streams `zlib`-compressed 24 MHz scanline SPI packets. Filename suffixes enforce safe exposure handling: raw `*_e1_nocor.pat` files allow GUI `exposureperline` repetition (scaling the 24 MHz `calc_step_half_period()` step timing) and `singlefacet` mode, whereas pre-sliced / optically corrected files such as `*_e4_cor.pat` already contain 4× (`0.25` step/line) scanlines and are never re-repeated.
  * **CNC Mode (`.gcode`, `.nc`, `.tap`):** On-board G-code interpreter supporting `G90`/`G91`, `G21`, `M3`/`M5` spindle control, and feedrates.
* **Peripherals & Diode Diagnostics:** Remote toggling/control for UV laser, rotating polygon prism, spindle PWM, cooling fan PWM, and photodiode facet jitter measurement (`test_diode` + saved reference `facetmeans`).
* **Live Camera & On-Board 2D Calibration:** OV2640 capture, saved inspection target coordinates, FOV scan visibility, single/all-facet dot preview, and automated 2D facet calibration & verification.
* **Settings & GitHub OTA:** Browser-based configuration for Wi-Fi, static IP, TMC2209/axis parameters, tool offsets, and direct-to-flash OTA updates with automatic `config.json` migration.

---

## Camera & Optical Calibration

The **Camera & Calibration** panel (`camera_panel.html`, `src/control/calibration.py`) shares all DSP and optical math with the main `hexastorm` package via `hexastorm.calibration.core`:

* **Live View & Manual Exposure:** Captures UXGA (`1600×1200`) JPEG snapshots on demand or via auto-refresh. Automatic DSP routines (AEC, AGC, AWB, BPC, WPC) are disabled so manual exposure times (`5 ms`–`1000 ms`, scaled against active frame height `aec_value`) keep laser spot measurements quantitative.
* **SVGA Grayscale Analysis:** `CameraController.analysis_mode()` switches temporarily to SVGA (`800×600`, `4.4 µm/px` effective pixel pitch) grayscale and captures dark frames for background subtraction, guarded by an `asyncio.Lock` so live snapshot refreshes never race FPGA SPI transfers.
* **Scan Visibility & Dot Preview (`F1`–`F4` / `All`):** Maps which `bitsinscanline` coordinates fall within the camera FOV (`visible_pixels`) and projects evenly spaced dots onto individual facets or all facets simultaneously.
* **Calibrate & Verify (2D):** Projects analytical 24 MHz dot events via `PrismCurveLite`, detects sub-pixel 2D centroids (`get_dots_ulab`), removes global camera tilt, computes spatial-median facet shifts using the unified `target - virtual_ref` sign convention (`analyze_facets_ulab`), persists per-facet `(scan, orth)` offsets to `config.json`, and runs `run_pattern_error()` (`slice_dot_grid_events()` + `VirtualStackerUlab`) to verify before/after residual 2D error is `≤ 30 µm`. On CPython, `src/camera.py` delegates analysis captures to `hexastorm.calibration.FakeScanhead` for realistic closed-loop offline simulation.

---

## Repository Map

| Path | Purpose |
| :--- | :--- |
| [src/boot.py](src/boot.py) | Early boot logger setup, asset deployment (`constants`), and RTC crash-loop guard |
| [src/main.py](src/main.py) | Async startup: Wi-Fi manager, NTP sync, optional WebREPL, Microdot server, and 5s stability mark |
| [src/control/webapp.py](src/control/webapp.py) | Microdot routes, session auth middleware, SSE `/state`, upload/delete, motion, and calibration API |
| [src/control/calibration.py](src/control/calibration.py) | OV2640 calibration orchestration, FPGA SPI lock, `24 MHz` dot projection, and 2D verification |
| [src/control/camera.py](src/control/camera.py) | `CameraController`, `AnalysisModeContext`, and OV2640 manual exposure register control |
| [src/control/laserhead/](src/control/laserhead/) | `BaseLaserhead` (CPython mock + shared `.pat`/G-code logic) and `Laserhead` (`ESP32Host` + TMC2209) |
| [src/control/bootlib.py](src/control/bootlib.py) | Wi-Fi STA/AP setup, NTP, GitHub OTA updater, disk/log helpers, and RTC crash counter |
| [src/control/constants.py](src/control/constants.py) | `SafeNVS`, `deploy_assets()`, config migration/merging, and hardware optical constants |
| [src/camera.py](src/camera.py) | CPython mock `camera` module backed by `hexastorm.calibration.FakeScanhead` |
| [src/manifest.py](src/manifest.py) | Packs `src/root/` via `freezefs` and freezes MicroPython + `hexastorm` modules |
| [src/build.py](src/build.py) | Local and `--remote` SSH MicroPython ESP32-S3 build and `esptool` flash CLI |
| [src/root/](src/root/) | Web UI HTML templates, `static/home.js`, factory `config.json`, and mock runtime files |
| [src/tests/](src/tests/) | Pytest Microdot endpoint, UI-contract, and `FakeScanhead` calibration tests + on-device scripts |

---

## Security

The webserver is protected by session authentication with default password **`hex`** (stored as salted SHA-256 hash in `src/root/config.json`). Change the password before exposing the controller on untrusted networks.

---

## Web Interface Overview

A visual overview of the webserver interface after successful login:

<img src="images/webserver.png" align="center" height="500"/>

---

## Local Development & Testing

Install dependencies with [uv](https://docs.astral.sh/uv/) and run the webserver locally using the CPython mock laserhead and `FakeScanhead` camera simulator (port `5000`, default password `hex`):

```bash
uv sync
uv run webapp
```

Run the automated test suite (`microdot.test_client.TestClient` HTTP endpoint tests, frontend template/JS route contract checks, `FakeScanhead` 2D calibration, and `.pat` packet streaming) and linter:

```bash
uv run pytest
uv run ruff check src
```

---

## Building & Flashing

See [developer.md](developer.md) for the full ESP-IDF / MicroPython setup, partition table, camera pin configuration, and REST API reference. Once configured, build and flash an ESP32-S3 locally or over SSH with:

```bash
uv run build
uv run build --remote user@host
```
