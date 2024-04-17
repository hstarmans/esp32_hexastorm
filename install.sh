rm -rf micrdot/__pycache__/
rm -rf templates/__pycache__/
rm -rf utemplates/__pycache__/
rm templates/*.py
# bootlib, utemplate, webapp via modules
mpremote connect /dev/ttyS23 fs cp constants.py :constants.py + fs cp config.json :config.json  + fs cp -r static :
mpremote connect /dev/ttyS23 fs cp -r templates : + fs cp boot.py :boot.py + mip install pyjwt + fs cp boot.py :boot.py
# ota update install
mpremote mip install github:glenn20/micropython-esp32-ota/mip/ota/mpy