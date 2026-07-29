from machine import Pin
import time
import gc
from camera import Camera, GrabMode, PixelFormat, FrameSize

print("Free memory (PSRAM check):", gc.mem_free(), "bytes")
if gc.mem_free() < 2000000:
    print("WARNING: Low memory! Is PSRAM recognized by your firmware?")

# 1. Initialize camera (Camera driver will manage hardware reset on Pin 42)
print("Configuring camera (JPEG mode, 10 MHz XCLK)...")
cam = Camera(
    data_pins=[12, 14, 21, 13, 11, 10, 9, 8],  # D0 through D7
    vsync_pin=41,
    href_pin=40,
    sda_pin=18,   # I2C Data (SIOD)
    scl_pin=17,   # I2C Clock (SIOC)
    pclk_pin=38,
    xclk_pin=39,
    xclk_freq=10000000,           # 10 MHz clock frequency (optimal sweet spot)
    pixel_format=PixelFormat.JPEG,# Hardware JPEG compression
    frame_size=FrameSize.UXGA,    # 1600x1200 (Highest native resolution for OV2640)
    jpeg_quality=85,              # High quality setting (85%)
    fb_count=1,                   # 1 frame buffer
    grab_mode=GrabMode.LATEST,
    powerdown_pin=-1,
    reset_pin=42,                 # Driver handles reset on GPIO 42
)

print("Camera initialized successfully!")

# 3. Sensor warm-up (AEC/AGC stabilization)
print("Warming up sensor (allowing Auto-Exposure / Auto-Gain to stabilize)...")
for i in range(5):
    buf = cam.capture()
    cam.free_buffer()
    time.sleep(0.1)

# 4. Capture picture
print("Capturing picture...")
img = cam.capture()
img_bytes = bytes(img)
cam.free_buffer()

print(f"Picture received: {len(img_bytes)} bytes")

# 5. JPEG Header & Footer Validation
if len(img_bytes) >= 4:
    has_header = img_bytes.startswith(b'\xff\xd8')
    has_footer = img_bytes.endswith(b'\xff\xd9')
    print(f"JPEG Header (0xFFD8) present: {has_header}")
    print(f"JPEG Footer (0xFFD9) present: {has_footer}")
    
    # On-device statistics (min, max, mean byte value)
    total_sum = sum(img_bytes)
    mean_val = total_sum / len(img_bytes)
    min_val = min(img_bytes)
    max_val = max(img_bytes)
    print(f"Byte statistics: Min={min_val}, Max={max_val}, Mean={mean_val:.2f}")

# 6. Save to file on ESP32
output_filename = "test_image.jpg"
print(f"Saving picture to '{output_filename}'...")
with open(output_filename, "wb") as f:
    f.write(img_bytes)

print(f"Done! Picture saved ({len(img_bytes)} bytes).")
print("\n--- DOWNLOAD INSTRUCTIONS ---")
print("Via Thonny: Refresh Files -> Right click test_image.jpg -> Save to computer")
print("Via mpremote: mpremote fs cp :test_image.jpg ./test_image.jpg")


