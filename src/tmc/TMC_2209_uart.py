import time
import sys
import struct
import logging

from machine import UART

logger = logging.getLogger(__name__)

#-----------------------------------------------------------------------
# TMC_UART
#
# this class is used to communicate with the TMC via UART
# it can be used to change the settings of the TMC.
# like the current or the microsteppingmode
#-----------------------------------------------------------------------
class TMC_UART:

    mtr_id=1
    ser = None
    rFrame  = [0x55, 0, 0, 0  ]
    wFrame  = [0x55, 0, 0, 0 , 0, 0, 0, 0 ]
    communication_pause = 0
    
#-----------------------------------------------------------------------
# constructor
#-----------------------------------------------------------------------
    def __init__(self, mtr_id, serialport=2, baudrate=115200):
        self.ser = UART(serialport, baudrate=115200, tx=43, rx=44) 
        self.mtr_id=mtr_id
        self.ser.init(115200 , bits=8, parity=None, stop=1)
        #self.ser.timeout = 20000/baudrate            # adjust per baud and hardware. Sequential reads without some delay fail.
        self.communication_pause = 500/baudrate     # adjust per baud and hardware. Sequential reads without some delay fail.

        #self.ser.reset_output_buffer()
        #self.ser.reset_input_buffer()
        
#-----------------------------------------------------------------------
# destructor
#-----------------------------------------------------------------------
    def __del__(self):
        self.ser.close()

#-----------------------------------------------------------------------
# this function calculates the crc8 parity bit
#-----------------------------------------------------------------------
    def compute_crc8_atm(self, datagram, initial_value=0):
        crc = initial_value
        # Iterate bytes in data
        for byte in datagram:
            # Iterate bits in byte
            for _ in range(0, 8):
                if (crc >> 7) ^ (byte & 0x01):
                    crc = ((crc << 1) ^ 0x07) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
                # Shift to next bit
                byte = byte >> 1
        return crc
    
#-----------------------------------------------------------------------
# reads the registry on the TMC with a given address.
# returns the binary value of that register
#-----------------------------------------------------------------------
    def read_reg(self, reg):
        
        rtn = ""
        #self.ser.reset_output_buffer()
        #self.ser.reset_input_buffer()
        
        self.rFrame[1] = self.mtr_id
        self.rFrame[2] = reg
        self.rFrame[3] = self.compute_crc8_atm(self.rFrame[:-1])

        rt = self.ser.write(bytes(self.rFrame))
        if rt != len(self.rFrame):
            logging.info("TMC2209: Err in write ", file=sys.stderr)
            return False
        time.sleep(self.communication_pause)  # adjust per baud and hardware. Sequential reads without some delay fail.
        if self.ser.any():
            rtn = self.ser.read()#read what it self 
        time.sleep(self.communication_pause)  # adjust per baud and hardware. Sequential reads without some delay fail.
        if rtn is None:
            logging.info("TMC2209: Err in read")
            return ""
#         print("received "+str(len(rtn))+" bytes; "+str(len(rtn)*8)+" bits")
        return(rtn[7:11])
#-----------------------------------------------------------------------
# this function tries to read the registry of the TMC 10 times
# if a valid answer is returned, this function returns it as an integer
#-----------------------------------------------------------------------
    def read_int(self, reg):
        tries = 0
        while(True):
            rtn = self.read_reg(reg)
            tries += 1
            if(len(rtn)>=4):
                break
            else:
                logging.info("TMC2209: did not get the expected 4 data bytes. Instead got "+str(len(rtn))+" Bytes")
            if(tries>=10):
                logging.info("TMC2209: after 10 tries not valid answer. exiting")
                logging.info("TMC2209: is Stepper Powersupply switched on ?")
                raise Exception("UART connection to stepper motors fails")
        val = struct.unpack(">i",rtn)[0]
        return(val)

#-----------------------------------------------------------------------
# this function can write a value to the register of the tmc
# 1. use read_int to get the current setting of the TMC
# 2. then modify the settings as wished
# 3. write them back to the driver with this function
#-----------------------------------------------------------------------
    def write_reg(self, reg, val):
        
        #self.ser.reset_output_buffer()
        #self.ser.reset_input_buffer()
        
        self.wFrame[1] = self.mtr_id
        self.wFrame[2] =  reg | 0x80;  # set write bit
        
        self.wFrame[3] = 0xFF & (val>>24)
        self.wFrame[4] = 0xFF & (val>>16)
        self.wFrame[5] = 0xFF & (val>>8)
        self.wFrame[6] = 0xFF & val
        
        self.wFrame[7] = self.compute_crc8_atm(self.wFrame[:-1])

        rtn = self.ser.write(bytes(self.wFrame))
        if rtn != len(self.wFrame):
            logging.info("TMC2209: Err in write", file=sys.stderr)
            return False
        time.sleep(self.communication_pause)

        return(True)

#-----------------------------------------------------------------------
# this function als writes a value to the register of the TMC
# but it also checks if the writing process was successfully by checking
# the InterfaceTransmissionCounter before and after writing
#-----------------------------------------------------------------------
    def write_reg_check(self, reg, val):
        IFCNT           =   0x02

        ifcnt1 = self.read_int(IFCNT)
        self.write_reg(reg, val)
        ifcnt2 = self.read_int(IFCNT)
        ifcnt2 = self.read_int(IFCNT)
        
        if(ifcnt1 >= ifcnt2):
            logging.info("TMC2209: writing not successful!")
            logging.info("reg:{} val:{}", reg, val)
            logging.info("ifcnt:",ifcnt1,ifcnt2)
            return False
        else:
            return True

#-----------------------------------------------------------------------
# this function clear the communication buffers of the Raspberry Pi
#-----------------------------------------------------------------------
    def flushSerialBuffer(self):
        #self.ser.reset_output_buffer()
        #self.ser.reset_input_buffer()
        return

#-----------------------------------------------------------------------
# this sets a specific bit to 1
#-----------------------------------------------------------------------
    def set_bit(self, value, bit):
        return value | (bit)

#-----------------------------------------------------------------------
# this sets a specific bit to 0
#-----------------------------------------------------------------------
    def clear_bit(self, value, bit):
        return value & ~(bit)

