#!/bin/bash
# USAGE:  ./install.sh /dev/tty8
rm -rf micrdot/__pycache__/
rm -rf templates/__pycache__/
rm -rf utemplates/__pycache__/
rm templates/*.py
# bootlib, utemplate, webapp via modules
mpremote connect $1 fs cp constants.py :constants.py + fs cp config.json :config.json  + fs cp -r static :
mpremote connect $1 fs cp -r templates : + fs cp boot.py :boot.py + fs cp webapp.py :webapp.py + mip install pyjwt
# ota update install
mpremote connect $1 mip install github:glenn20/micropython-esp32-ota/mip/ota/mpy
# install Winbond Flash library
mpremote connect $1 mip install github:brainelectronics/micropython-winbond
# without sdcard
# mpremote connect $1 mkdir sd
# mpremote connect $1 mkdir sd/files