import machine
import os
import network


def mount_sd():
    ''' mounts SDCard and changes working directory '''
    try:
        sd = machine.SDCard(slot=2)
        os.mount(sd, '/sd')
        os.chdir('/sd')
    except OSError:
        print("""Cannot connect to sdcard"""
              """Hard reboot is required, not mounted""")
        return


def connect_wifi(wifi_login=None, verbose=False):
    ''' connects to wifi

        wifi_login: dictionary with keys ssid and password
    '''
    if not wifi_login:
        try:
            import secrets
            wifi_login = secrets.wifi_login
        except ImportError:
            print("Cannot find secret.py on SDCard.")
            print("Not connected to WIFI.")
    sta_if = network.WLAN(network.STA_IF)
    if not sta_if.isconnected():
        print('Connecting to network...')
        sta_if.active(True)
        sta_if.connect(wifi_login['ssid'],
                       wifi_login['password'])
        while not sta_if.isconnected():
            pass
    if verbose:
        print('Network config:', sta_if.ifconfig())
