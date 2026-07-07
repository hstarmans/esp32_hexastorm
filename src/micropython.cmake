# This top-level micropython.cmake is responsible for 
# integrating ulab and camera driver

# Ulab
include(${CMAKE_CURRENT_LIST_DIR}/micropython-ulab/code/micropython.cmake)

# Camera
include(${CMAKE_CURRENT_LIST_DIR}/micropython-camera-API/micropython.cmake)
