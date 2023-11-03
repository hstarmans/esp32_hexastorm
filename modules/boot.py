# file is ran only once at boot
# keep minimal, mistakes can prevent board from booting
import bootlib
bootlib.mount_sd()
bootlib.connect_wifi()
