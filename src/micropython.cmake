# This top-level micropython.cmake integrates ulab and the camera driver
# from tracked Git submodules in deps/.

# Ulab
include(${CMAKE_CURRENT_LIST_DIR}/../deps/micropython-ulab/code/micropython.cmake)

# Vector Core (C99 scanline-polygon intersection kernel)
include(${CMAKE_CURRENT_LIST_DIR}/../deps/hexastorm/src/hexastorm/exposure/csrc/micropython.cmake)

# Camera (preserve MICROPY_FROZEN_MANIFEST, which micropython-camera-API overwrites)
set(_HEXASTORM_SAVED_MANIFEST "${MICROPY_FROZEN_MANIFEST}")
include(${CMAKE_CURRENT_LIST_DIR}/../deps/micropython-camera-API/micropython.cmake)
if(_HEXASTORM_SAVED_MANIFEST)
    set(MICROPY_FROZEN_MANIFEST "${_HEXASTORM_SAVED_MANIFEST}")
endif()

# Ensure the esp32-camera IDF component is linked into usermod_mp_camera and
# visible to MicroPython's QSTR/header preprocessor.
idf_component_get_property(esp32_camera_lib esp32-camera COMPONENT_LIB)
target_link_libraries(usermod_mp_camera INTERFACE ${esp32_camera_lib})
micropy_gather_target_properties(${esp32_camera_lib})

# Disable TinyUSB to use the native USB Serial/JTAG which supports hardware resets over DTR/RTS
add_compile_definitions(MICROPY_HW_ENABLE_USBDEV=0)
