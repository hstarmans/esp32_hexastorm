# Create an INTERFACE library for our CPP module.
add_library(usermod_steppers INTERFACE)

# Add our source files to the library.
target_sources(usermod_steppers INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/steppers.cpp
    ${CMAKE_CURRENT_LIST_DIR}/steppersmodule.c
    ${CMAKE_CURRENT_LIST_DIR}/src/source/CHOPCONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/COOLCONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/DRV_CONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/DRV_STATUS.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/DRVCONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/DRVCTRL.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/DRVSTATUS.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/ENCMODE.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/GCONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/IHOLD_IRUN.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/PWMCONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/RAMP_STAT.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/SERIAL_SWITCH.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/SGCSCONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/SHORT_CONF.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/SMARTEN.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/SW_MODE.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/SW_SPI.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/TMC2130Stepper.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/TMC2160Stepper.cpp
    #${CMAKE_CURRENT_LIST_DIR}/src/source/TMC2208Stepper.cpp
    #${CMAKE_CURRENT_LIST_DIR}/src/source/TMC2209Stepper.cpp
    #${CMAKE_CURRENT_LIST_DIR}/src/source/TMC2660Stepper.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/TMC5130Stepper.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/TMC5160Stepper.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/source/TMCStepper.cpp
)

#  esp32 library for micropython
# -D flag is lost somewhere further down, i have therefore also 
# hardcoded define
add_definitions(-Wall -Desp32)

# Add the current directory as an include directory.
target_include_directories(usermod_steppers INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${CMAKE_CURRENT_LIST_DIR}/src
)

# Link our INTERFACE library to the usermod target.
target_link_libraries(usermod INTERFACE usermod_steppers)
