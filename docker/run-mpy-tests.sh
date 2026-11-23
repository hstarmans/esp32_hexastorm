#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
MICROPYTHON_DIR="${MICROPYTHON_DIR:-/opt/micropython}"
MPY_BUILD_DIR="${MPY_BUILD_DIR:-${WORKSPACE_DIR}/build/micropython-unix}"
MPY_BIN="${MPY_BUILD_DIR}/micropython"
MPY_HEAPSIZE="${MPY_HEAPSIZE:-8M}"
USERMODS_STAGE="${MPY_BUILD_DIR}/usermods"

cleanup() {
    if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ]; then
        chown -R "${HOST_UID}:${HOST_GID}" "${MPY_BUILD_DIR}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

HEXASTORM_SRC=""
if [ -n "${HEXASTORM_DIR:-}" ]; then
    if [ -f "${HEXASTORM_DIR}/src/hexastorm/__init__.py" ]; then
        HEXASTORM_SRC="${HEXASTORM_DIR}/src"
    elif [ -f "${HEXASTORM_DIR}/hexastorm/__init__.py" ]; then
        HEXASTORM_SRC="${HEXASTORM_DIR}"
    else
        echo "ERROR: Invalid HEXASTORM_DIR=${HEXASTORM_DIR}" >&2
        exit 1
    fi
else
    HEXASTORM_SRC="${WORKSPACE_DIR}/deps/hexastorm/src"
fi

if [ -x "${MPY_BIN}" ] && ! "${MPY_BIN}" -c "import _vector_core" >/dev/null 2>&1; then
    echo "Rebuilding MicroPython unix port to link _vector_core C module..."
    rm -rf "${MPY_BUILD_DIR}"
fi

mkdir -p "${USERMODS_STAGE}"
ln -sfn "${WORKSPACE_DIR}/deps/micropython-ulab/code" "${USERMODS_STAGE}/ulab"
ln -sfn "${HEXASTORM_SRC}/hexastorm/exposure/csrc" "${USERMODS_STAGE}/vector_core"

if [ ! -x "${MPY_BIN}" ]; then
    echo "Building MicroPython unix port with ulab + _vector_core..."
fi
BUILD_LOG="/tmp/mpy_unix_build.log"
if ! make -C "${MICROPYTHON_DIR}/ports/unix" \
    -j"$(nproc)" \
    VARIANT=standard \
    MICROPY_PY_FFI=0 \
    MICROPY_PY_BTREE=0 \
    MICROPY_PY_SSL=0 \
    MICROPY_SSL_MBEDTLS=0 \
    MICROPY_PY_CRYPTOLIB=0 \
    BUILD="${MPY_BUILD_DIR}" \
    USER_C_MODULES="${USERMODS_STAGE}" \
    >"${BUILD_LOG}" 2>&1; then
    cat "${BUILD_LOG}" >&2
    exit 1
fi

export MPY_WORKSPACE="${WORKSPACE_DIR}"
export MPY_HEXASTORM_SRC="${HEXASTORM_SRC}"
export MPY_STDLIB_ROOT="${MICROPYTHON_DIR}/lib/micropython-lib/python-stdlib"

"${MPY_BIN}" -X "heapsize=${MPY_HEAPSIZE}" "${WORKSPACE_DIR}/src/tests/mpy_test_calibration.py"
