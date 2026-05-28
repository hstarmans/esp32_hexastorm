from machine import UART, Pin
import time

# check chip outputs 5V and 29V is vmot
# internal diode voltage vref is 1.52V

# 1. Reset pins (Discharging)
tx_reset = Pin(15, Pin.OUT)
rx_reset = Pin(7, Pin.OUT)
tx_reset.value(0)
rx_reset.value(0)
time.sleep(1.0)

tx_reset.init(Pin.IN)
rx_reset.init(Pin.IN)

# 2. Start UART
uart = UART(2, baudrate=9600, tx=Pin(15), rx=Pin(7), timeout=50)

# 3. Wake up the motor via EN pin
step_enable = Pin(16, Pin.OUT)
step_enable.value(0)
time.sleep(0.2)


def calc_crc(data):
    crc = 0
    for byte in data:
        b = byte
        for _ in range(8):
            if ((crc >> 7) ^ (b & 0x01)) & 0x01:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
            b >>= 1
    return crc


print("--- Start TMC2209 Radar Scanner ---")

# 4. Poll all possible IDs
for slave_id in range(4):
    # 0x55 = Sync, slave_id = Address, 0x00 = Register (GCONF)
    req = bytearray([0x55, slave_id, 0x00])
    req.append(calc_crc(req))  # Add the correct checksum

    # Clear buffer
    while uart.any():
        uart.read()

    # Send request
    uart.write(req)

    # Give the TMC a moment to send back 8 bytes
    time.sleep(0.05)

    if uart.any():
        res = uart.read()
        if res and len(res) > 4:
            print(f"✅ BINGO! Chip found on ID {slave_id}!")
            # Show the bytes nicely as hexadecimal
            print(f"   Received data: {[hex(x) for x in res]}")
        elif res and req == res:
            print(f"❌ ID {slave_id}: Only echo ({len(res)} bytes). Chip is silent.")
        else:
            print(f"❓ Received something else on ID {slave_id}: {res}")
    else:
        print(f"⚠️ ID {slave_id}: Completely dead silent.")

print("--- Scan Complete ---")
