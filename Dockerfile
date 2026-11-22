FROM espressif/idf:v5.5.2

ARG MICROPYTHON_VERSION=v1.28.0
ENV MICROPYTHON_DIR=/opt/micropython

# Install Python asset tools directly inside ESP-IDF's environment so manifest.py
# runs hermetically without requiring uv or a host virtualenv inside Docker.
RUN bash -lc '. "${IDF_PATH}/export.sh" && pip install --no-cache-dir "freezefs>=2.4" "pyclean>=3.1.0"'

# Allow git describe/status checks on bind-mounted host repositories.
RUN git config --system --add safe.directory '*'

# Pre-clone MicroPython, build mpy-cross, and populate ESP32 port submodules once.
RUN git clone --depth 1 --branch "${MICROPYTHON_VERSION}" https://github.com/micropython/micropython.git "${MICROPYTHON_DIR}" \
    && git -C "${MICROPYTHON_DIR}" submodule update --init --recursive --depth 1 \
    && make -C "${MICROPYTHON_DIR}/mpy-cross" -j"$(nproc)" \
    && bash -lc '. "${IDF_PATH}/export.sh" && make -C "${MICROPYTHON_DIR}/ports/esp32" BOARD=ESP32_GENERIC_S3 submodules && rm -rf "${MICROPYTHON_DIR}/ports/esp32/build-ESP32_GENERIC_S3"'

ENV MICROPY_MPYCROSS=${MICROPYTHON_DIR}/mpy-cross/build/mpy-cross

# Isolated Python venv for Amaranth/YoWASP iCE40UP5K FPGA bitstream synthesis
# (kept separate from ESP-IDF's Python env to avoid dependency conflicts).
ENV FPGA_VENV=/opt/fpga-venv
RUN python3 -m venv "${FPGA_VENV}" \
    && "${FPGA_VENV}/bin/pip" install --no-cache-dir \
        "amaranth>=0.5.10,<0.6" \
        "yowasp-yosys>=0.71.0.0.post1285" \
        "yowasp-nextpnr-ice40>=0.11.1.0.post826" \
        "numpy>=2.0"

COPY docker/build-firmware.sh /usr/local/bin/build-firmware.sh
COPY docker/build_fpga.py /usr/local/bin/build_fpga.py
RUN chmod +x /usr/local/bin/build-firmware.sh /usr/local/bin/build_fpga.py

WORKDIR /workspace
CMD ["/usr/local/bin/build-firmware.sh"]
