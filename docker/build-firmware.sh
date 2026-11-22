#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
BOARD_NAME="${BOARD_NAME:-HEXASTORM_S3}"
BUILD_DIR="${BUILD_DIR:-${WORKSPACE_DIR}/build/${BOARD_NAME}}"
FPGA_VENV="${FPGA_VENV:-/opt/fpga-venv}"
FPGA_BUILD_DIR="${FPGA_BUILD_DIR:-${WORKSPACE_DIR}/build/fpga}"
FPGA_OUT="${WORKSPACE_DIR}/src/root/sd/fpga/fpga.bit"

if [ "${SKIP_FPGA_BUILD:-0}" != "1" ]; then
    PATH="${FPGA_VENV}/bin:${PATH}" "${FPGA_VENV}/bin/python" /usr/local/bin/build_fpga.py \
        --workspace "${WORKSPACE_DIR}" \
        --build-dir "${FPGA_BUILD_DIR}" \
        --output "${FPGA_OUT}"
fi

if [ -z "${IDF_PYTHON_ENV_PATH:-}" ]; then
    # shellcheck disable=SC1091
    . "${IDF_PATH}/export.sh"
fi

mkdir -p "$(dirname "${BUILD_DIR}")"
export MICROPY_MPYCROSS="${MICROPY_MPYCROSS:-${MICROPYTHON_DIR}/mpy-cross/build/mpy-cross}"

make -C "${MICROPYTHON_DIR}/ports/esp32" \
    -j"$(nproc)" \
    BOARD="${BOARD_NAME}" \
    BOARD_DIR="${WORKSPACE_DIR}/boards/${BOARD_NAME}" \
    BUILD="${BUILD_DIR}" \
    FROZEN_MANIFEST="${WORKSPACE_DIR}/src/manifest.py" \
    USER_C_MODULES="${WORKSPACE_DIR}/src/micropython.cmake"

if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ]; then
    chown -R "${HOST_UID}:${HOST_GID}" \
        "${BUILD_DIR}" \
        "${FPGA_BUILD_DIR}" \
        "${WORKSPACE_DIR}/src/root/sd/fpga" \
        "${WORKSPACE_DIR}/src/control/frozen_root.py" \
        "${WORKSPACE_DIR}/src/control/build_info.py" 2>/dev/null || true
fi
