from machine import Pin, I2C, PWM
import time

# --- YOUR EXACT PINS FROM THE SCHEMATIC ---
PIN_SIOD = 18  # SDA
PIN_SIOC = 17  # SCL
PIN_XCLK = 39  # DVP_XCLK
PIN_RESET = 42  # CAM_RST

print("Preparing camera...")

# 1. Take the camera out of Reset (pull CAM_RST high)
# From your previous schematic, we know PWDN is tied to GND via hardware,
# so we don't need to control it in software.
reset_pin = Pin(PIN_RESET, Pin.OUT)
reset_pin.value(1)  # 1 = High = Active

time.sleep(0.1)  # Short hardware delay

# 2. Start external clock (DVP_XCLK)
print("Starting clock (XCLK) on GPIO 39...")
xclk_pwm = PWM(Pin(PIN_XCLK))
xclk_pwm.freq(10000000)  # 10 MHz clock signal
xclk_pwm.duty_u16(32768)  # 50% duty cycle (square wave)

time.sleep(0.2)  # Wait briefly for the camera's internal logic to stabilize

# 3. Scan I2C bus
print("Scanning I2C bus on GPIO 18 (SDA) and 17 (SCL)...")
try:
    # Init I2C bus 0 at a safe speed of 100kHz
    i2c = I2C(0, scl=Pin(PIN_SIOC), sda=Pin(PIN_SIOD), freq=100000)
    devices = i2c.scan()

    if len(devices) == 0:
        print("[-] No I2C device found. Please check your physical connections.")
    else:
        for device in devices:
            print(f"    -> Device found at hex address: {hex(device)}")
            if device == 0x30 or device == 48:
                print("       *** SUCCESS! OV2640 CAMERA DETECTED (0x30)! ***")
except Exception as e:
    print("Error configuring I2C communication:", e)

finally:
    # Clean up the clock neatly after testing to prevent conflicts
    xclk_pwm.deinit()
