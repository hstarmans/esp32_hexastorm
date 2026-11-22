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

COPY docker/build-firmware.sh /usr/local/bin/build-firmware.sh
RUN chmod +x /usr/local/bin/build-firmware.sh

WORKDIR /workspace
CMD ["/usr/local/bin/build-firmware.sh"]
