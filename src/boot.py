# --- boot.py ---
import logging

# Note: importing control.bootlib imports control.constants, which runs deploy_assets()
# on ESP32 to extract assets and migrate any pre-existing config_old.json.
from control import bootlib

# set log level for bootloader
logger = logging.getLogger(__name__)
bootlib.set_log_level(logging.INFO)

# Hardware / Crash checks
bootlib.check_crash_loop_rtc()
