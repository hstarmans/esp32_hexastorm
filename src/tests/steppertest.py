from time import sleep

from machine import Pin

from tmc.stepperdriver import *


pin_en = 38   # enable pin
mtr_ids = [0, 1]    # uart id
use_hex = True


en = Pin(pin_en, Pin.OUT)

if use_hex:
    print("using hexastorm library")
    from hexastorm.controller import Host
    hst = Host(micropython=True)
    for _ in range(3):
        print("Test stages cannot move, press enter")
        hst.enable_steppers = True
        input()
        print("Stages can move, press enter")
        hst.enable_steppers = False
        input()
else:
    print("using this script")
    for mtr_id in mtr_ids:
        tmc = TMC_2209(pin_en=pin_en, mtr_id=mtr_id)
        tmc.setDirection_reg(False)
        tmc.setVSense(True)
        tmc.setCurrent(100)
        tmc.setIScaleAnalog(True)
        tmc.setInterpolation(True)
        tmc.setSpreadCycle(False)
        tmc.setMicrosteppingResolution(16)
        tmc.setInternalRSense(False)
        tmc.setMotorEnabled(False)

    for _ in range(3):
        print("Test stages cannot move, press enter")
        en.value(0)
        input()
        print("Stages can move, press enter")
        en.value(1)
        input()

