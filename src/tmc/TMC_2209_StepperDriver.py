import math

import machine
from machine import Pin as GPIO

from .TMC_2209_uart import TMC_UART
from . import TMC_2209_reg as reg


class Direction():
    CCW = 0
    CW = 1

class Loglevel():
    none = 0
    error = 10
    info = 20
    debug = 30
    movement = 40
    all = 100

class MovementAbsRel():
    absolute = 0
    relative = 1

#-----------------------------------------------------------------------
# TMC_2209
#
# this class has two different functions:
# 1. change setting in the TMC-driver via UART
# 2. move the motor via STEP/DIR pins
#-----------------------------------------------------------------------
class TMC_2209:
    
    tmc_uart = None
    _pin_en = -1
    p_pin_en = -1
    
    _direction = True

    _stop = False

    _msres = -1
    _stepsPerRevolution = 0
    
    _loglevel = Loglevel.none

    _currentPos = 0                 # current position of stepper in steps
    _targetPos = 0                  # the target position in steps
    _speed = 0.0                    # the current speed in steps per second
    _maxSpeed = 1.0                 # the maximum speed in steps per second
    _maxSpeedHoming = 500           # the maximum speed in steps per second for homing
    _acceleration = 1.0             # the acceleration in steps per second per second
    _accelerationHoming = 10000     # the acceleration in steps per second per second for homing
    _sqrt_twoa = 1.0                # Precomputed sqrt(2*_acceleration)
    _stepInterval = 0               # the current interval between two steps
    _minPulseWidth = 1              # minimum allowed pulse with in microseconds
    _lastStepTime = 0               # The last step time in microseconds
    _n = 0                          # step counter
    _c0 = 0                         # Initial step size in microseconds
    _cn = 0                         # Last step size in microseconds
    _cmin = 0                       # Min step size in microseconds based on maxSpeed
    _sg_threshold = 100             # threshold for stallguard
    _movement_abs_rel = MovementAbsRel.absolute
    
    def mean(obj, x):
        a = 0
        for v in x:
           a=a+v
        return(a/len(x))

#-----------------------------------------------------------------------
# constructor
#-----------------------------------------------------------------------
    def __init__(self, pin_en, mtr_id=1):
        
        self.tmc_uart = TMC_UART(mtr_id=mtr_id)
        self._pin_en = pin_en
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: Init")
        self.p_pin_en = GPIO(self._pin_en, GPIO.OUT)
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: GPIO Init finished")      
        self.readStepsPerRevolution()
        self.clearGSTAT()
        self.tmc_uart.flushSerialBuffer()
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: Init finished")


#-----------------------------------------------------------------------
# destructor
#-----------------------------------------------------------------------
    def __del__(self):
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: Deinit")
        self.setMotorEnabled(False)
        GPIO.cleanup() 

#-----------------------------------------------------------------------
# set the loglevel. See the Enum Loglevel
#-----------------------------------------------------------------------       
    def setLoglevel(self, loglevel):
        self._loglevel = loglevel

#-----------------------------------------------------------------------
# set whether the movment should be relative or absolute by default.
# See the Enum MovementAbsoluteRelative
#-----------------------------------------------------------------------       
    def setMovementAbsRel(self, movement_abs_rel):
        self._movement_abs_rel = movement_abs_rel

#-----------------------------------------------------------------------
# read the register Adress "DRVSTATUS" and prints all current setting
#-----------------------------------------------------------------------
    def readDRVSTATUS(self):
        print("TMC2209: ---")
        print("TMC2209: DRIVER STATUS:")
        drvstatus =self.tmc_uart.read_int(reg.DRVSTATUS)
        if(self._loglevel >= Loglevel.info):
            print("TMC2209:", bin(drvstatus))
        if(drvstatus & reg.stst):
            print("TMC2209: Info: motor is standing still")
        else:
            print("TMC2209: Info: motor is running")

        if(drvstatus & reg.stealth):
            print("TMC2209: Info: motor is running on StealthChop")
        else:
            print("TMC2209: Info: motor is running on SpreadCycle")

        cs_actual = drvstatus & reg.cs_actual
        cs_actual = cs_actual >> 16
        print("TMC2209: CS actual: "+str(cs_actual))

        if(drvstatus & reg.olb):
            print("TMC2209: Warning: Open load detected on phase B")
        
        if(drvstatus & reg.ola):
            print("TMC2209: Warning: Open load detected on phase A")
        
        if(drvstatus & reg.s2vsb):
            print("TMC2209: Error: Short on low-side MOSFET detected on phase B. The driver becomes disabled")

        if(drvstatus & reg.s2vsa):
            print("TMC2209: Error: Short on low-side MOSFET detected on phase A. The driver becomes disabled")

        if(drvstatus & reg.s2gb):
            print("TMC2209: Error: Short to GND detected on phase B. The driver becomes disabled. ")
        
        if(drvstatus & reg.s2ga):
            print("TMC2209: Error: Short to GND detected on phase A. The driver becomes disabled. ")
        
        if(drvstatus & reg.ot):
            print("TMC2209: Error: Driver Overheating!")
        
        if(drvstatus & reg.otpw):
            print("TMC2209: Warning: Driver Overheating Prewarning!")
        
        print("---")
        return drvstatus
            
#-----------------------------------------------------------------------
# read the register Adress "GCONF" and prints all current setting
#-----------------------------------------------------------------------
    def readGCONF(self):
        print("TMC2209: ---")
        print("TMC2209: GENERAL CONFIG")
        gconf = self.tmc_uart.read_int(reg.GCONF)
        if(self._loglevel >= Loglevel.info):
            print("TMC2209:", bin(gconf))

        if(gconf & reg.i_scale_analog):
            print("TMC2209: Driver is using voltage supplied to VREF as current reference")
        else:
            print("TMC2209: Driver is using internal reference derived from 5VOUT")
        if(gconf & reg.internal_rsense):
            print("TMC2209: Internal sense resistors. Use current supplied into VREF as reference.")
            print("TMC2209: VREF pin internally is driven to GND in this mode.")
            print("TMC2209: This will most likely destroy your driver!!!")
            raise SystemExit
        else:
            print("TMC2209: Operation with external sense resistors")
        if(gconf & reg.en_spreadcycle):
            print("TMC2209: SpreadCycle mode enabled")
        else:
            print("TMC2209: StealthChop PWM mode enabled")
        if(gconf & reg.shaft):
            print("TMC2209: Inverse motor direction")
        else:
            print("TMC2209: normal motor direction")
        if(gconf & reg.index_otpw):
            print("TMC2209: INDEX pin outputs overtemperature prewarning flag")
        else:
            print("TMC2209: INDEX shows the first microstep position of sequencer")
        if(gconf & reg.index_step):
            print("TMC2209: INDEX output shows step pulses from internal pulse generator")
        else:
            print("TMC2209: INDEX output as selected by index_otpw")
        if(gconf & reg.mstep_reg_select):
            print("TMC2209: Microstep resolution selected by MSTEP register")
        else:
            print("TMC2209: Microstep resolution selected by pins MS1, MS2")
        
        print("TMC2209: ---")
        return gconf

#-----------------------------------------------------------------------
# read the register Adress "GSTAT" and prints all current setting
#-----------------------------------------------------------------------
    def readGSTAT(self):
        print("TMC2209: ---")
        print("TMC2209: GSTAT")
        gstat = self.tmc_uart.read_int(reg.GSTAT)
        if(self._loglevel >= Loglevel.info):
            print("TMC2209:",bin(gstat))
        if(gstat & reg.reset):
            print("TMC2209: The Driver has been reset since the last read access to GSTAT")
        if(gstat & reg.drv_err):
            print("TMC2209: The driver has been shut down due to overtemperature or short circuit detection since the last read access")
        if(gstat & reg.uv_cp):
            print("TMC2209: Undervoltage on the charge pump. The driver is disabled in this case")
        print("TMC2209: ---")
        return gstat

#-----------------------------------------------------------------------
# read the register Adress "GSTAT" and prints all current setting
#-----------------------------------------------------------------------
    def clearGSTAT(self):        
        gstat = self.tmc_uart.read_int(reg.GSTAT)
        gstat = self.tmc_uart.set_bit(gstat, reg.reset)
        gstat = self.tmc_uart.set_bit(gstat, reg.drv_err)
        self.tmc_uart.write_reg_check(reg.GSTAT, gstat)

#-----------------------------------------------------------------------
# read the register Adress "IOIN" and prints all current setting
#-----------------------------------------------------------------------
    def readIOIN(self):
        print("TMC2209: ---")
        print("TMC2209: INPUTS")
        ioin = self.tmc_uart.read_int(reg.IOIN)
        if(self._loglevel >= Loglevel.info):
            print("TMC2209:", bin(ioin))
        if(ioin & reg.io_spread):
            print("TMC2209: spread is high")
        else:
            print("TMC2209: spread is low")

        if(ioin & reg.io_dir):
            print("TMC2209: dir is high")
        else:
            print("TMC2209: dir is low")

        if(ioin & reg.io_step):
            print("TMC2209: step is high")
        else:
            print("TMC2209: step is low")

        if(ioin & reg.io_enn):
            print("TMC2209: en is high")
        else:
            print("TMC2209: en is low")
        
        print("TMC2209: ---")
        return ioin

#-----------------------------------------------------------------------
# read the register Adress "CHOPCONF" and prints all current setting
#-----------------------------------------------------------------------
    def readCHOPCONF(self):
        print("TMC2209: ---")
        print("TMC2209: CHOPPER CONTROL")
        chopconf = self.tmc_uart.read_int(reg.CHOPCONF)
        if(self._loglevel >= Loglevel.info):
            print("TMC2209:", bin(chopconf))
        
        print("TMC2209: native "+str(self.getMicroSteppingResolution())+" microstep setting")
        
        if(chopconf & reg.intpol):
            print("TMC2209: interpolation to 256 microsteps")
        
        if(chopconf & reg.vsense):
            print("TMC2209: 1: High sensitivity, low sense resistor voltage")
        else:
            print("TMC2209: 0: Low sensitivity, high sense resistor voltage")

        print("TMC2209: ---")
        return chopconf

#-----------------------------------------------------------------------
# enables or disables the motor current output
#-----------------------------------------------------------------------
    def setMotorEnabled(self, en):
        if en:
            self.p_pin_en.off()
        else:
            self.p_pin_en.on()
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: Motor output active: {}".format(en))   

#-----------------------------------------------------------------------
# homes the motor in the given direction using stallguard
#-----------------------------------------------------------------------
    def doHoming(self, direction, threshold=None):
        sg_results = []
        
        if(threshold is not None):
            self._sg_threshold = threshold
        
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: ---")
            print("TMC2209: homing")
        
        if(self._loglevel >= Loglevel.debug):
            print("TMC2209: Stallguard threshold:",self._sg_threshold)
        
        self.setDirection_pin(direction)
        self.setSpreadCycle(0)

        if (direction == 1):
            self._targetPos = self._stepsPerRevolution * 10
        else:
            self._targetPos = -self._stepsPerRevolution * 10
        self._stepInterval = 0
        self._speed = 0.0
        self._n = 0
        self.setAcceleration(10000)
        self.setMaxSpeed(self._maxSpeedHoming)
        self.computeNewSpeed()
        step_counter=0
        #print("TMC2209: Steps per Revolution: "+str(self._stepsPerRevolution))
        while (step_counter<self._stepsPerRevolution):
            if (self.runSpeed()): #returns true, when a step is made
                step_counter += 1
                self.computeNewSpeed()
                sg_result = self.getStallguard_Result()
                sg_results.append(sg_result)
                if(len(sg_results)>20):
                    sg_result_average = self.mean(sg_results[-6:])
                    if(sg_result_average < self._sg_threshold):
                        break

        if(step_counter<self._stepsPerRevolution):
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: homing successful")
                print("TMC2209: Stepcounter: "+str(step_counter))
            if(self._loglevel >= Loglevel.debug):
                print("TMC2209: Stepcounter: "+str(step_counter))
                print(sg_results)
            self._currentPos = 0
        else:
            if(self._loglevel >= Loglevel.error):
                print("TMC2209: homing failed")
            if(self._loglevel >= Loglevel.debug):
                print("TMC2209: Stepcounter: "+str(step_counter))
                print(sg_results)
        
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: ---")
        
#-----------------------------------------------------------------------
# returns the current motor position in microsteps
#-----------------------------------------------------------------------
    def getCurrentPosition(self):
        return self._currentPos

#-----------------------------------------------------------------------
# overwrites the current motor position in microsteps
#-----------------------------------------------------------------------
    def setCurrentPosition(self, newPos):
        self._currentPos = newPos

#-----------------------------------------------------------------------
# returns the motor shaft direction: 0 = CCW; 1 = CW
#-----------------------------------------------------------------------
    def getDirection_reg(self):
        gconf = self.tmc_uart.read_int(reg.GCONF)
        return (gconf & reg.shaft)

#-----------------------------------------------------------------------
# sets the motor shaft direction to the given value: 0 = CCW; 1 = CW
#-----------------------------------------------------------------------
    def setDirection_reg(self, direction):        
        gconf = self.tmc_uart.read_int(reg.GCONF)
        if(direction):
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: write inverse motor direction")
            gconf = self.tmc_uart.set_bit(gconf, reg.shaft)
        else:
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: write normal motor direction")
            gconf = self.tmc_uart.clear_bit(gconf, reg.shaft)
        self.tmc_uart.write_reg_check(reg.GCONF, gconf)
  
#-----------------------------------------------------------------------
# return whether Vref (1) or 5V (0) is used for current scale
#-----------------------------------------------------------------------
    def getIScaleAnalog(self):
        gconf = self.tmc_uart.read_int(reg.GCONF)
        return (gconf & reg.i_scale_analog)

#-----------------------------------------------------------------------
# sets Vref (1) or 5V (0) for current scale
#-----------------------------------------------------------------------
    def setIScaleAnalog(self,en):        
        gconf = self.tmc_uart.read_int(reg.GCONF)
        if(en):
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated Vref for current scale")
            gconf = self.tmc_uart.set_bit(gconf, reg.i_scale_analog)
        else:
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated 5V-out for current scale")
            gconf = self.tmc_uart.clear_bit(gconf, reg.i_scale_analog)
        self.tmc_uart.write_reg_check(reg.GCONF, gconf)

#-----------------------------------------------------------------------
# returns which sense resistor voltage is used for current scaling
# 0: Low sensitivity, high sense resistor voltage
# 1: High sensitivity, low sense resistor voltage
#-----------------------------------------------------------------------
    def getVSense(self):
        chopconf = self.tmc_uart.read_int(reg.CHOPCONF)
        return (chopconf & reg.vsense)

#-----------------------------------------------------------------------
# sets which sense resistor voltage is used for current scaling
# 0: Low sensitivity, high sense resistor voltage
# 1: High sensitivity, low sense resistor voltage
#-----------------------------------------------------------------------
    def setVSense(self,en):      
        chopconf = self.tmc_uart.read_int(reg.CHOPCONF)
        if(en):
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated High sensitivity, low sense resistor voltage")
            chopconf = self.tmc_uart.set_bit(chopconf, reg.vsense)
        else:
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated Low sensitivity, high sense resistor voltage")
            chopconf = self.tmc_uart.clear_bit(chopconf, reg.vsense)
        self.tmc_uart.write_reg_check(reg.CHOPCONF, chopconf)

#-----------------------------------------------------------------------
# returns which sense resistor voltage is used for current scaling
# 0: Low sensitivity, high sense resistor voltage
# 1: High sensitivity, low sense resistor voltage
#-----------------------------------------------------------------------
    def getInternalRSense(self):
        gconf = self.tmc_uart.read_int(reg.GCONF)
        return (gconf & reg.internal_rsense)

#-----------------------------------------------------------------------
# sets which sense resistor voltage is used for current scaling
# 0: Low sensitivity, high sense resistor voltage
# 1: High sensitivity, low sense resistor voltage
#-----------------------------------------------------------------------
    def setInternalRSense(self,en):        
        gconf = self.tmc_uart.read_int(reg.GCONF)
        if(en):
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated internal sense resistors.")
            gconf = self.tmc_uart.set_bit(gconf, reg.internal_rsense)
        else:
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated operation with external sense resistors")
            gconf = self.tmc_uart.clear_bit(gconf, reg.internal_rsense)
        self.tmc_uart.write_reg_check(reg.GCONF, gconf)

#-----------------------------------------------------------------------
# sets the current scale (CS) for Running and Holding
# and the delay, when to be switched to Holding current
# IHold = 0-31; IRun = 0-31; IHoldDelay = 0-15
#-----------------------------------------------------------------------
    def setIRun_Ihold(self, IHold, IRun, IHoldDelay):
        ihold_irun = 0
        
        ihold_irun = ihold_irun | IHold << 0
        ihold_irun = ihold_irun | IRun << 8
        ihold_irun = ihold_irun | IHoldDelay << 16
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: ihold_irun: ", bin(ihold_irun))
            #print(bin(ihold_irun))
            print("TMC2209: writing ihold_irun")
        self.tmc_uart.write_reg_check(reg.IHOLD_IRUN, ihold_irun)
        
#-----------------------------------------------------------------------
# sets the current flow for the motor
# run_current in mA
# check whether Vref is actually 1.2V
#-----------------------------------------------------------------------
    def setCurrent(self, run_current, hold_current_multiplier = 0.5, hold_current_delay = 10, Vref = 1.2):
        CS_IRun = 0
        Rsense = 0.11
        Vfs = 0

        if(self.getVSense()):
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: Vsense: 1")
            Vfs = 0.180 * Vref / 2.5
        else:
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: Vsense: 0")
            Vfs = 0.325 * Vref / 2.5
            
        CS_IRun = 32.0*1.41421*run_current/1000.0*(Rsense+0.02)/Vfs - 1

        CS_IRun = min(CS_IRun, 31)
        CS_IRun = max(CS_IRun, 0)
        
        CS_IHold = hold_current_multiplier * CS_IRun

        CS_IRun = round(CS_IRun)
        CS_IHold = round(CS_IHold)
        hold_current_delay = round(hold_current_delay)

        if(self._loglevel >= Loglevel.info):
            print("TMC2209: CS_IRun: " + str(CS_IRun))
            print("TMC2209: CS_IHold: " + str(CS_IHold))
            print("TMC2209: Delay: " + str(hold_current_delay))

        self.setIRun_Ihold(CS_IHold, CS_IRun, hold_current_delay)

#-----------------------------------------------------------------------
# return whether spreadcycle (1) is active or stealthchop (0)
#-----------------------------------------------------------------------
    def getSpreadCycle(self):
        gconf = self.tmc_uart.read_int(reg.GCONF)
        return (gconf & reg.en_spreadcycle)

#-----------------------------------------------------------------------
# enables spreadcycle (1) or stealthchop (0)
#-----------------------------------------------------------------------
    def setSpreadCycle(self,en_spread):
        gconf = self.tmc_uart.read_int(reg.GCONF)
        if(en_spread):
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated Spreadcycle")
            gconf = self.tmc_uart.set_bit(gconf, reg.en_spreadcycle)
        else:
            if(self._loglevel >= Loglevel.info):
                print("TMC2209: activated Stealthchop")
            gconf = self.tmc_uart.clear_bit(gconf, reg.en_spreadcycle)
        self.tmc_uart.write_reg_check(reg.GCONF, gconf)

#-----------------------------------------------------------------------
# return whether the tmc inbuilt interpolation is active
#-----------------------------------------------------------------------
    def getInterpolation(self):
        chopconf = self.tmc_uart.read_int(reg.CHOPCONF)
        if(chopconf & reg.intpol):
            return True
        else:
            return False

#-----------------------------------------------------------------------
# enables the tmc inbuilt interpolation of the steps to 256 microsteps
#-----------------------------------------------------------------------
    def setInterpolation(self, en):
        chopconf = self.tmc_uart.read_int(reg.CHOPCONF)

        if(en):
            chopconf = self.tmc_uart.set_bit(chopconf, reg.intpol)
        else:
            chopconf = self.tmc_uart.clear_bit(chopconf, reg.intpol)

        if(self._loglevel >= Loglevel.info):
            print("TMC2209: writing microstep interpolation setting: "+str(en))
        self.tmc_uart.write_reg_check(reg.CHOPCONF, chopconf)

#-----------------------------------------------------------------------
# returns the current native microstep resolution (1-256)
#-----------------------------------------------------------------------
    def getMicroSteppingResolution(self):
        chopconf = self.tmc_uart.read_int(reg.CHOPCONF)
        msresdezimal = chopconf & (reg.msres0 | reg.msres1 | reg.msres2 | reg.msres3)
        msresdezimal = msresdezimal >> 24
        msresdezimal = 8 - msresdezimal
        self._msres = int(math.pow(2, msresdezimal))
        return self._msres

#-----------------------------------------------------------------------
# sets the current native microstep resolution (1,2,4,8,16,32,64,128,256)
#-----------------------------------------------------------------------
    def setMicrosteppingResolution(self, msres):      
        chopconf = self.tmc_uart.read_int(reg.CHOPCONF)
        chopconf = chopconf & (~reg.msres0 | ~reg.msres1 | ~reg.msres2 | ~reg.msres3) #setting all bits to zero
        msresdezimal = int(math.log(msres, 2))
        msresdezimal = 8 - msresdezimal
        chopconf = int(chopconf) & int(4043309055)
        chopconf = chopconf | msresdezimal <<24
        
        if(self._loglevel >= Loglevel.info):
            print("TMC2209: writing "+str(msres)+" microstep setting")
        self.tmc_uart.write_reg_check(reg.CHOPCONF, chopconf)
        self.setMStepResolutionRegSelect(True)
        self.readStepsPerRevolution()
        return True
        
#-----------------------------------------------------------------------
# sets the register bit "mstep_reg_select" to 1 or 0 depending to the given value.
# this is needed to set the microstep resolution via UART
# this method is called by "setMicrosteppingResolution"
#-----------------------------------------------------------------------
    def setMStepResolutionRegSelect(self, en):                  
        gconf = self.tmc_uart.read_int(reg.GCONF)
        
        if en:
            gconf = self.tmc_uart.set_bit(gconf, reg.mstep_reg_select)
        else:
            gconf = self.tmc_uart.clear_bit(gconf, reg.mstep_reg_select)

        if(self._loglevel >= Loglevel.info):
            print("TMC2209: writing MStep Reg Select: "+str(en))
        self.tmc_uart.write_reg_check(reg.GCONF, gconf)

#-----------------------------------------------------------------------
# returns how many steps are needed for one revolution
#-----------------------------------------------------------------------
    def readStepsPerRevolution(self):
        self._stepsPerRevolution = 200*self.getMicroSteppingResolution()
        return self._stepsPerRevolution

#-----------------------------------------------------------------------
# returns how many steps are needed for one revolution
#-----------------------------------------------------------------------
    def getStepsPerRevolution(self):
        return self._stepsPerRevolution

#-----------------------------------------------------------------------
# reads the interface transmission counter from the tmc register
# this value is increased on every succesfull write access
# can be used to verify a write access
#-----------------------------------------------------------------------
    def getInterfaceTransmissionCounter(self):
        ifcnt = self.tmc_uart.read_int(reg.IFCNT)
        if(self._loglevel >= Loglevel.debug):
            print("TMC2209: Interface Transmission Counter: "+str(ifcnt))
        return ifcnt

#-----------------------------------------------------------------------
# return the current stallguard result
# its will be calculated with every fullstep
# higher values means a lower motor load
#-----------------------------------------------------------------------
    def getTStep(self):
        tstep = self.tmc_uart.read_int(reg.TSTEP)
        return tstep

#-----------------------------------------------------------------------
# return the current stallguard result
# its will be calculated with every fullstep
# higher values means a lower motor load
#-----------------------------------------------------------------------
    def getStallguard_Result(self):
        sg_result = self.tmc_uart.read_int(reg.SG_RESULT)
        return sg_result

#-----------------------------------------------------------------------
# sets the register bit "SGTHRS" to to a given value
# this is needed for the stallguard interrupt callback
# SG_RESULT becomes compared to the double of this threshold.
# SG_RESULT ≤ SGTHRS*2
#-----------------------------------------------------------------------
    def setStallguard_Threshold(self, threshold):

        if(self._loglevel >= Loglevel.info):
            print("TMC2209: sgthrs")
            print(bin(threshold))

            print("TMC2209: writing sgthrs")
        self.tmc_uart.write_reg_check(reg.SGTHRS, threshold)

#-----------------------------------------------------------------------
# This  is  the  lower  threshold  velocity  for  switching  
# on  smart energy CoolStep and StallGuard to DIAG output. (unsigned)
#-----------------------------------------------------------------------
    def setCoolStep_Threshold(self, threshold):

        if(self._loglevel >= Loglevel.info):
            print("TMC2209: tcoolthrs")
            print(bin(threshold))

            print("TMC2209: writing tcoolthrs")
        self.tmc_uart.write_reg_check(reg.TCOOLTHRS, threshold)

#-----------------------------------------------------------------------
# set a function to call back, when the driver detects a stall 
# via stallguard
# high value on the diag pin can also mean a driver error
#-----------------------------------------------------------------------
    def setStallguard_Callback(self, pin_stallguard, threshold, my_callback, min_speed = 2000):

        self.setStallguard_Threshold(threshold)
        self.setCoolStep_Threshold(min_speed)

        if(self._loglevel >= Loglevel.info):
            print("TMC2209: setup stallguard callback")
        
        #GPIO.setup(pin_stallguard, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)    
        #GPIO.add_event_detect(pin_stallguard, GPIO.RISING, callback=my_callback, bouncetime=300) 
        p25 = machine.Pin(pin_stallguard, machine.Pin.IN, machine.Pin.PULL_DOWN)
        p25.irq(trigger=machine.Pin.IRQ_RISING, handler=my_callback)
#-----------------------------------------------------------------------
# returns the current Microstep counter.
# Indicates actual position in the microstep table for CUR_A
#-----------------------------------------------------------------------
    def getMicrostepCounter(self):
        mscnt = self.tmc_uart.read_int(reg.MSCNT)
        return mscnt

#-----------------------------------------------------------------------
# returns the current Microstep counter.
# Indicates actual position in the microstep table for CUR_A
#-----------------------------------------------------------------------
    def getMicrostepCounterInSteps(self, offset=0):
        step = (self.getMicrostepCounter()-64)*(self._msres*4)/1024
        step = (4*self._msres)-step-1
        step = round(step)
        return step+offset

#-----------------------------------------------------------------------
# sets the maximum motor speed in steps per second
#-----------------------------------------------------------------------
    def setMaxSpeed(self, speed):
        if (speed < 0.0):
           speed = -speed
        if (self._maxSpeed != speed):
            self._maxSpeed = speed
            self._cmin = 1000000.0 / speed
            # Recompute _n from current speed and adjust speed if accelerating or cruising
            if (self._n > 0):
                self._n = (self._speed * self._speed) / (2.0 * self._acceleration) # Equation 16
                self.computeNewSpeed()

#-----------------------------------------------------------------------
# returns the maximum motor speed in steps per second
#-----------------------------------------------------------------------
    def getMaxSpeed(self):
        return self._maxSpeed

#-----------------------------------------------------------------------
# sets the motor acceleration/decceleration in steps per sec per sec
#-----------------------------------------------------------------------
    def setAcceleration(self, acceleration):
        if (acceleration == 0.0):
            return
        if (acceleration < 0.0):
          acceleration = -acceleration
        if (self._acceleration != acceleration):
            # Recompute _n per Equation 17
            self._n = self._n * (self._acceleration / acceleration)
            # New c0 per Equation 7, with correction per Equation 15
            self._c0 = 0.676 * math.sqrt(2.0 / acceleration) * 1000000.0 # Equation 15
            self._acceleration = acceleration
            self.computeNewSpeed()

#-----------------------------------------------------------------------
# returns the motor acceleration/decceleration in steps per sec per sec
#-----------------------------------------------------------------------
    def getAcceleration(self):
        return self._acceleration

#-----------------------------------------------------------------------
# stop the current movement
#-----------------------------------------------------------------------
    def stop(self):
        self._stop = True

