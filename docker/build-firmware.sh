#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
BOARD_NAME="${BOARD_NAME:-HEXASTORM_S3}"
BUILD_DIR="${BUILD_DIR:-${WORKSPACE_DIR}/build/${BOARD_NAME}}"

if [ -z "${IDF_PYTHON_ENV_PATH:-}" ]; then
    # shellcheck disable=SC1091
    . "${IDF_PATH}/export.sh"
fi

mkdir -p "$(dirname "${BUILD_DIR}")"

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
        "${WORKSPACE_DIR}/src/control/frozen_root.py" \
        "${WORKSPACE_DIR}/src/control/build_info.py" 2>/dev/null || true
fi
