# This top-level micropython.cmake is responsible for 
# integrating TMCStepper, fomuflash and micropython-ulab
# into the esp32 binary

# Fomuflash
# include(${CMAKE_CURRENT_LIST_DIR}/fomuflash/micropython.cmake)

# Ulab
include(${CMAKE_CURRENT_LIST_DIR}/micropython-ulab/code/micropython.cmake)

# TMCStepper
include(${CMAKE_CURRENT_LIST_DIR}/TMCStepper/micropython.cmake)
