# Python Runtime & Tests

- Use `uv` when running Python commands (e.g. `uv run ...`).
- Do not run or look for `pytest` as it is not installed/defined.

# Hardware Tests & Obscure Cases


- `src/control/tests/test_hardware.py`:
  - Contains hardware-level verification tests for FPGA SPI communication and SPI Flash programming.
  - `test_spirepeat`: Tests SPI echo/communication with the FPGA. Requires a special FPGA bitstream (`reply.bit`, generated via `old/debug_spi/test_spi.py`).
  - `test_write_blink_toflash`: Flashes `blink.bit` to SPI Flash RAM to verify FPGA auto-configuration and LED status.
  - Do not run these tests as part of standard automated unit tests unless connected to physical hardware with the appropriate test bitstreams loaded.
