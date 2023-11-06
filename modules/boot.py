# file is ran only once at boot
# keep minimal, mistakes can prevent board from booting
import bootlib
bootlib.mount_sd()
made_connection = bootlib.connect_wifi()
if made_connection:
    bootlib.start_webrepl()
