# Python Runtime & Tests

- Use `uv` when running Python commands (e.g. `uv run ...`).
- Do not run or look for `pytest` as it is not installed/defined.
- Do not try to run unit tests (tests rely on MicroPython target environments and physical hardware).

# Coding Style & Error Handling (Fail-Fast & Centralized Constants)

- **Do NOT use `dict.get("key", default)` to silently fall back on missing keys** in internal data structures, configuration dicts (`CONFIG`), hardware configs (`laserhead.cfg`), or expected API payloads.
- Always use direct indexing `dct["key"]` so that a missing or misspelled key raises a `KeyError` immediately (fail-fast) rather than silently substituting a dummy value (like `0` or `{}`) that hides bugs and makes debugging harder.
- Ensure new configuration keys are explicitly added to `src/root/config.json` and `src/root/mock_config.json` instead of guarding them with `.get(...)` fallbacks in code.
- **Do NOT scatter physical or system constants across feature modules** (such as `calibration.py`). Keep minimal, primary physical/optical constants centralized at the top of `src/control/constants.py` (or in `PlatformConfig` in `hexastorm/config.py`), and derive secondary values (e.g. binned pixel pitch or `lanewidth` via `laserhead.cfg.get_optical_params()["lanewidth"]`) from those primary parameters.

# Hardware Tests & Obscure Cases


- `src/control/tests/test_hardware.py`:
  - Contains hardware-level verification tests for FPGA SPI communication and SPI Flash programming.
  - `test_spirepeat`: Tests SPI echo/communication with the FPGA. Requires a special FPGA bitstream (`reply.bit`, generated via `old/debug_spi/test_spi.py`).
  - `test_write_blink_toflash`: Flashes `blink.bit` to SPI Flash RAM to verify FPGA auto-configuration and LED status.
  - Do not run these tests as part of standard automated unit tests unless connected to physical hardware with the appropriate test bitstreams loaded.
