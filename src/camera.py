"""camera.py - Compatibility stub and CPython mock for the MicroPython 'camera' module.

On ESP32 hardware, MicroPython's built-in C-module 'camera' takes precedence.
On host CPython, this provides a dynamic Pillow-based mock image generator for development.
"""

import io
import math
from datetime import datetime

from PIL import Image, ImageDraw


class FrameSize:
    QQVGA = 1
    QVGA = 6
    VGA = 10
    SVGA = 11
    HD = 13
    SXGA = 14
    UXGA = 15


class PixelFormat:
    RGB565 = 0
    YUV422 = 1
    GRAYSCALE = 3
    JPEG = 4
    RAW = 6


class GrabMode:
    WHEN_EMPTY = 0
    LATEST = 1


class Camera:
    """Mock implementation of the MicroPython Camera class for CPython."""

    def __init__(self, *args, **kwargs):
        self.exposure_ctrl = False
        self.gain_ctrl = False
        self.aec_value = 150
        self.agc_gain = 0
        self._capture_count = 0

    def capture(self):
        """Generates a dynamic mock JPEG image in memory."""
        self._capture_count += 1
        w, h = 640, 480
        img = Image.new("RGB", (w, h), color=(10, 10, 16))
        draw = ImageDraw.Draw(img)

        # Background grid
        for x in range(0, w, 80):
            draw.line([(x, 0), (x, h)], fill=(25, 25, 35), width=1)
        for y in range(0, h, 60):
            draw.line([(0, y), (w, y)], fill=(25, 25, 35), width=1)

        # Center crosshair
        draw.line([(w // 2, h // 2 - 25), (w // 2, h // 2 + 25)], fill=(60, 60, 80), width=1)
        draw.line([(w // 2 - 25, h // 2), (w // 2 + 25, h // 2)], fill=(60, 60, 80), width=1)

        now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        draw.text((15, 15), f"MOCK CAMERA TEST - FRAME #{self._capture_count}", fill=(0, 255, 160))
        draw.text((15, 32), f"Timestamp: {now_str}  |  PlatformConfig Pins OK", fill=(200, 200, 200))
        draw.text((15, 48), "F1..F4 = Facets of rotating polygon mirror", fill=(130, 150, 180))

        # Animated facet sweep lines (F1..F4)
        base_ys = [180, 220, 260, 300]
        for idx, by in enumerate(base_ys):
            wobble = int(math.sin(self._capture_count * 0.9 + idx * 1.5) * 6)
            fy = by + wobble
            draw.line([(60, fy), (580, fy)], fill=(40, 80, 240), width=5)
            draw.line([(70, fy), (570, fy)], fill=(120, 190, 255), width=3)
            draw.line([(80, fy), (560, fy)], fill=(255, 255, 255), width=1)
            draw.text((20, fy - 7), f"F{idx + 1}", fill=(160, 210, 255))
            draw.text((595, fy - 7), f"{fy}px", fill=(100, 120, 140))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def free_buffer(self):
        pass

    def deinit(self):
        pass
