# Microdot Webserver for Dual-Mode PCB Fabrication (Laser & CNC)

This project provides a Microdot webserver running on an ESP32-S3 microcontroller, enabling remote control of a high-precision, dual-mode PCB fabrication workstation. 

The system is designed to take you from a raw copper-clad board to a finished prototype by supporting two powerful fabrication methods on a single machine:

1. **UV Laser Lithography (Exposure):** High-speed, high-resolution direct imaging onto photoresist-coated substrates using a synchronized rotating polygon mirror.
2. **Mechanical CNC Milling (Spindle Routing):** Physical trace carving, via drilling, and board contour routing using a spindle motor and standard G-code.

The core of the machine's motion control is handled by a Lattice iCE40 UP5K FPGA, running pipelined Discrete Forward Differencing programmed with the [Hexastorm](https://github.com/hstarmans/hexastorm) Amaranth HDL toolchain.

---

## Features

* **Coordinated Multi-Axis Motion:** Fully coordinated 3D linear interpolation (`G0`/`G1`) across all axes for precise routing and carving.
* **Smart Job Launcher:** Auto-detects whether an uploaded file is a laser exposure job or a CNC G-code job, dynamically updating the web UI options accordingly.
* **Hardware Abstraction:**
  * **CNC Mode:** Leverages a lightweight on-board G-code parser supporting absolute/relative positioning (`G90`/`G91`), millimeter mode (`G21`), spindle speed control (`M3`/`M5`), and feedrates.
  * **Laser Mode:** Seamlessly streams high-frequency packed scanlines and manages precise polygon sync.
* **Peripherals Control:** Remote control over UV laser state, rotating prism, spindle speed, cooling fans, and led diagnostics.
* **Workspace Operations:** Easily set local workspace zeros (WPOS) and home axes against physical limit switches.
* **Comprehensive Machine Configuration:** A dedicated Settings UI to manage Wi-Fi credentials, static IP allocation, TMC motor driver calibrations, and tool offsets without recompiling code.
* **Over-The-Air (OTA) Updates:** Check for and apply the latest firmware releases directly from GitHub via the web interface, completely bypassing physical USB connections and button presses.

---

## Security

For security, the webserver is protected by a default password: **`hex`**.

---

## Web Interface Overview

A visual overview of the webserver interface after successful login:

<img src="images/webserver.png" align="center" height="500"/>

---

## Testing Locally

You can test and interact with the web UI locally on your machine without flashing the ESP32. 

Install the project dependencies using [uv](https://docs.astral.sh/uv/), and then spin up the local webserver:

```bash
# Install dependencies
uv sync
# Run the webserver locally
uv run python -m src.control.webapp
```

## Building & Flashing

Comprehensive installation, toolchain setup, and environment configuration instructions can be found in [developer.md](developer.md).

Once your environment is set up and board directory paths in `src/build.py` are verified, connect your ESP32-S3 board and run:

```bash
uv run build
```

This automated workflow will:
1. Detect your connected Espressif board.
2. Put the board into bootloader mode.
3. Build the custom MicroPython firmware.
4. Flash the firmware image to the device.
5. Reboot the board back into MicroPython.