# Vendored MicroPython Packages

Pure-Python third-party libraries required both on ESP32-S3 (frozen via `src/manifest.py`) and locally under CPython (`pytest` / `uv run webapp`) are kept in `vendor/` rather than `src/` so application code stays separated from external packages.

| Package | Version / Source | Upstream | License | Local Modifications |
| :--- | :--- | :--- | :--- | :--- |
| `microdot` | `2.7.0` | https://github.com/miguelgrinberg/microdot | MIT | Unmodified |
| `utemplate` | Paul Sokolovsky `utemplate` | https://github.com/pfalcon/utemplate | MIT | Unmodified |
| `ota` | `0.2` (`mip/ota`) | https://github.com/glenn20/micropython-esp32-ota | MIT | Unmodified |
| `mrequests` | `urequests` (`micropython-lib` PR #278 lineage) | https://github.com/micropython/micropython-lib | MIT | Patched in `vendor/mrequests/urequests.py` (see below) |
| `winbond` | `0.5.2` | https://github.com/brainelectronics/micropython-winbond | MIT | Unmodified |
| `tmc` | Custom / Hexastorm TMC2209 UART driver | Internal (`stepperdriver.py`, `uart.py`, `reg.py`) | MIT | Tailored for FPGA STEP/DIR + UART diagnostics |

## Local Patches

### `mrequests` (`vendor/mrequests/urequests.py`)
* Uses MicroPython 1.24+ `tls.SSLContext(tls.PROTOCOL_TLS_CLIENT)` (`CERT_NONE`) for HTTPS requests instead of legacy `ussl.wrap_socket`.
* Follows HTTP `301`, `302`, `303`, `307`, and `308` redirects (required for GitHub Releases asset downloads during OTA updates).
* Provides context-manager resource cleanup and streaming `Response.iter_content(chunk_size)` / `iter_lines()` so `bootlib.update_firmware()` can stream binaries directly to the ESP32 OTA partition without buffering into RAM.
