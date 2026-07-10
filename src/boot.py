# --- boot.py ---
import os
import json
import logging
import ota.rollback
from control import bootlib, constants

# set log level for bootloader
logger = logging.getLogger(__name__)
bootlib.set_log_level(logging.INFO)

# Hardware / Crash checks
bootlib.check_crash_loop_rtc()

# Bootloader / OTA logica
if constants.ESP32 and False:
    # bootloader needs to keep booting from this partition
    ota.rollback.cancel()
    try:
        with open("config_old.json") as f:
            dct2 = json.load(f)
        constants.CONFIG.update(dct2)
        constants.update_json()
        os.remove("config_old.json")
    except OSError:
        pass
