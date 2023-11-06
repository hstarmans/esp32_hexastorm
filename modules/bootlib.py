import os
import machine
import network
from time import sleep
import esp
import webrepl


def mount_sd():
    ''' mounts SDCard and changes working directory '''
    try:
        sd = machine.SDCard(slot=2)
        os.mount(sd, '/sd')
        os.chdir('/sd')
    except OSError:
        print("""Cannot connect to sdcard.\n"""
              """Hard reboot is required, not mounted""")


def get_secrets():
    ''' retrieve secrets from SDCard '''
    try:
        import secrets
        wifi_login = secrets.wifi_login
    except ImportError:
        print("""Cannot find secrets.py on SDCard.\n"""
                """Not connected to WIFI.""")
        wifi_login = None
    return wifi_login


def start_webrepl(wifi_login=None):
    ''' start webrepl 
    
        wifi_login: dictionary with key webrepl_password
    '''
    # disable debug output
    # recommended when using webrepl
    esp.osdebug(None)
    wifi_login = get_secrets()
    if wifi_login:
        webrepl.start(password=wifi_login['webrepl_password'])
    else:
        print("Webrepl not started. \n Password not provided.")


def connect_wifi(wifi_login=None):
    ''' connects to wifi and enables web repl

        wifi_login: dictionary with keys ssid, password,
                    static ip, dnsmask, static enabled,
                    gateway ip

        returns boolean: True if connected
    '''
    made_connection = False
    wifi_login = get_secrets()
    wlan = network.WLAN(network.STA_IF)
    if machine.reset_cause() != machine.SOFT_RESET:
        wlan.active(True)
        if wifi_login and wifi_login['static_enabled']:
            wlan.ifconfig(config=(wifi_login['static_ip'],
                                  wifi_login['dnsmask'],
                                  wifi_login['gateway_ip'],
                                  wifi_login['primary_dns']))
    if wlan.isconnected():
        made_connection = True
    elif (not wlan.isconnected()) and wifi_login: 
        print('Connecting to network...')
        wlan.active(True)
        wlan.connect(wifi_login['ssid'], 
                     wifi_login['password'])
        max_wait = 10
        while max_wait > 0:
            if wlan.isconnected():
                made_connection = True
                break
            else:
                max_wait -= 1
                sleep(1)
    if made_connection:
        print('Network config:', wlan.ifconfig())
    else:
        print("Cannot connect to wifi!")  
    return made_connection
