# This top-level micropython.cmake integrates ulab and the camera driver
# from tracked Git submodules in deps/.

# Ulab
include(${CMAKE_CURRENT_LIST_DIR}/../deps/micropython-ulab/code/micropython.cmake)

# Camera
include(${CMAKE_CURRENT_LIST_DIR}/../deps/micropython-camera-API/micropython.cmake)

# Disable TinyUSB to use the native USB Serial/JTAG which supports hardware resets over DTR/RTS
add_compile_definitions(MICROPY_HW_ENABLE_USBDEV=0)
