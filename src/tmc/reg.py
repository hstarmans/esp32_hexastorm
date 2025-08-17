# -----------------------------------------------------------------------
# TMC2209 register map and bit fields
#
# This file exposes:
#  - Register addresses (e.g., GCONF, GSTAT, CHOPCONF)
#  - Bit masks for each register field (lowercase names kept for compat)
#  - Helpful combined masks and shifts (UPPERCASE) for common fields
#
# Notes:
#  - "write-1-to-clear" applies to GSTAT.reset/drv_err
#  - Microstep resolution uses CHOPCONF.msres[3:0] encoded as 0..8
# -----------------------------------------------------------------------

# ------------------------ Register addresses ---------------------------
GCONF = 0x00
GSTAT = 0x01
IFCNT = 0x02
IOIN = 0x06
IHOLD_IRUN = 0x10
TSTEP = 0x12
TCOOLTHRS = 0x14
SGTHRS = 0x40
SG_RESULT = 0x41
MSCNT = 0x6A
CHOPCONF = 0x6C
DRVSTATUS = 0x6F

# ------------------------ GCONF bits -----------------------------------
i_scale_analog = 1 << 0  # Use VREF (True) vs internal ref (False)
internal_rsense = 1 << 1  # Use internal Rsense (danger on most boards)
en_spreadcycle = 1 << 2  # True=SpreadCycle, False=StealthChop
shaft = 1 << 3  # Invert motor direction
index_otpw = 1 << 4  # INDEX shows OTPW instead of step pos
index_step = 1 << 5  # INDEX outputs step pulses
mstep_reg_select = 1 << 7  # True: microstep via register; False: MS1/MS2 pins

# ------------------------ GSTAT bits -----------------------------------
reset = 1 << 0  # Write 1 to clear
drv_err = 1 << 1  # Write 1 to clear
uv_cp = 1 << 2  # Undervoltage on charge pump (read-only)

# ------------------------ CHOPCONF bits --------------------------------
vsense = 1 << 17  # True: high sensitivity, low sense voltage
msres0 = 1 << 24
msres1 = 1 << 25
msres2 = 1 << 26
msres3 = 1 << 27
intpol = 1 << 28  # Interpolate to 256 usteps

# Convenience:
MSRES_MASK = msres0 | msres1 | msres2 | msres3
MSRES_SHIFT = 24

# ------------------------ IOIN bits ------------------------------------
io_enn = 1 << 0  # ENN pin level (1=high)
io_step = 1 << 7  # STEP pin level
io_spread = 1 << 8  # SPREAD pin level
io_dir = 1 << 9  # DIR pin level

# ------------------------ DRVSTATUS bits -------------------------------
stst = 1 << 31  # Motor is standing still
stealth = 1 << 30  # Running in StealthChop
cs_actual = 31 << 16  # Actual current scale (5 bits)
t157 = 1 << 11  # Overtemp flags (past)
t150 = 1 << 10
t143 = 1 << 9
t120 = 1 << 8
olb = 1 << 7  # Open load B
ola = 1 << 6  # Open load A
s2vsb = 1 << 5  # Short low-side MOSFET B
s2vsa = 1 << 4  # Short low-side MOSFET A
s2gb = 1 << 3  # Short to GND B
s2ga = 1 << 2  # Short to GND A
ot = 1 << 1  # Overtemperature (shutdown)
otpw = 1 << 0  # Overtemperature prewarning

# Convenience:
CS_ACTUAL_SHIFT = 16
CS_ACTUAL_MASK = 0x1F << CS_ACTUAL_SHIFT

# ------------------------ IHOLD_IRUN bits ------------------------------
ihold = 31 << 0  # bits [4:0]
irun = 31 << 8  # bits [12:8]
iholddelay = 15 << 16  # bits [19:16]

IHOLD_SHIFT = 0
IRUN_SHIFT = 8
IHOLDDELAY_SHIFT = 16

# ------------------------ SGTHRS ---------------------------------------
sgthrs = 255 << 0  # bits [7:0]

# ------------------------ Microstep resolution aliases -----------------
# CHOPCONF.msres encoding per datasheet:
mres_256 = 0
mres_128 = 1
mres_64 = 2
mres_32 = 3
mres_16 = 4
mres_8 = 5
mres_4 = 6
mres_2 = 7
mres_1 = 8
