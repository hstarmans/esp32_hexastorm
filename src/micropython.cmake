# This top-level micropython.cmake is responsible for 
# integrating ulab and camera driver

# Ulab
include(${CMAKE_CURRENT_LIST_DIR}/micropython-ulab/code/micropython.cmake)

# Camera
include(${CMAKE_CURRENT_LIST_DIR}/micropython-camera-API/micropython.cmake)

# Disable TinyUSB to use the native USB Serial/JTAG which supports hardware resets over DTR/RTS
add_compile_definitions(MICROPY_HW_ENABLE_USBDEV=0)
