set(IDF_TARGET esp32s3)

configure_file(
    ${MICROPY_BOARD_DIR}/sdkconfig.board.in
    ${CMAKE_BINARY_DIR}/sdkconfig.hexastorm
    @ONLY
)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.ble
    boards/sdkconfig.spiram_sx
    boards/sdkconfig.240mhz
    boards/sdkconfig.spiram_oct
    ${CMAKE_BINARY_DIR}/sdkconfig.hexastorm
)

list(APPEND EXTRA_COMPONENT_DIRS "${MICROPY_BOARD_DIR}/../../deps/esp32-camera")
list(APPEND IDF_COMPONENTS esp32-camera)
