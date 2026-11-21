"""camera.py - Compatibility stub and CPython mock for the MicroPython 'camera' module.

On ESP32 hardware, MicroPython's built-in C-module 'camera' takes precedence.
On host CPython, this delegates grayscale analysis captures to
``hexastorm.calibration.simulator.FakeScanhead`` so calibration loops exercise true
first-order optical physics and 24 MHz clock-tick events end-to-end.
"""

import io
import math
from datetime import datetime
from typing import ClassVar

from PIL import Image, ImageDraw

from control.constants import OV2640_PIXEL_SIZE_UM, SVGA_PIXEL_SIZE_UM
from hexastorm import ulabext
from hexastorm.calibration import FakeScanhead, PrismCurveLite
from hexastorm.config import PlatformConfig


class FrameSize:
    QQVGA = 1
    QVGA = 6
    VGA = 10
    SVGA = 11
    HD = 13
    SXGA = 14
    UXGA = 15


FRAME_SIZE_DIMS = {
    FrameSize.QQVGA: (160, 120),
    FrameSize.QVGA: (320, 240),
    FrameSize.VGA: (640, 480),
    FrameSize.SVGA: (800, 600),
    FrameSize.HD: (1280, 720),
    FrameSize.SXGA: (1280, 1024),
    FrameSize.UXGA: (1600, 1200),
}


class PixelFormat:
    RGB565 = 0
    YUV422 = 1
    GRAYSCALE = 3
    JPEG = 4
    RAW = 6


class GrabMode:
    WHEN_EMPTY = 0
    LATEST = 1


def _draw_gaussian_spot_gray(buf, w, h, cx, cy, sigma=3.2, peak=230.0, radius=10):
    """Render a continuous sub-pixel 2D Gaussian laser spot into a grayscale bytearray."""
    icx = round(cx)
    icy = round(cy)
    inv_2sig2 = 1.0 / (2.0 * sigma * sigma)
    y0 = max(0, icy - radius)
    y1 = min(h - 1, icy + radius)
    x0 = max(0, icx - radius)
    x1 = min(w - 1, icx + radius)

    for py in range(y0, y1 + 1):
        dy2 = (py - cy) * (py - cy)
        row_off = py * w
        for px in range(x0, x1 + 1):
            dx2 = (px - cx) * (px - cx)
            add_val = int(peak * math.exp(-(dx2 + dy2) * inv_2sig2))
            if add_val > 0:
                cur = buf[row_off + px]
                buf[row_off + px] = min(255, cur + add_val)


class Camera:
    """Mock implementation of the MicroPython Camera class for CPython."""

    mock_laser_events = None
    mock_laser_pattern = None
    mock_active_facet = 0
    mock_v_stage_mm = 0.0
    mock_preview_facet = None
    mock_rotation_deg = 1.5
    mock_facet_offsets_um: ClassVar[dict[int, tuple[float, float]]] = {
        0: (14.0, -9.0),
        1: (-44.0, 36.0),
        2: (26.0, -24.0),
        3: (-12.0, 13.0),
    }
    mock_apply_correction = False
    mock_calibrated_facets: ClassVar[dict] = {}
    _mock_params: ClassVar[dict | None] = None
    _mock_curve: ClassVar[PrismCurveLite | None] = None

    def __init__(self, *args, **kwargs):
        self.frame_size = kwargs.get("frame_size", FrameSize.UXGA)
        self.pixel_format = kwargs.get("pixel_format", PixelFormat.JPEG)
        self.jpeg_quality = kwargs.get("jpeg_quality", 85)
        self.fb_count = kwargs.get("fb_count", 1)
        self.grab_mode = kwargs.get("grab_mode", GrabMode.LATEST)

        self.exposure_ctrl = False
        self.aec2 = False
        self.gain_ctrl = False
        self.agc_gain = 0
        self.whitebal = False
        self.awb_gain = False
        self.bpc = False
        self.wpc = False
        self.aec_value = 1200
        self._capture_count = 0
        self._scanhead_cache: dict[tuple[int, int, float], FakeScanhead] = {}

    @classmethod
    def _ensure_optical_model(cls) -> tuple[dict, PrismCurveLite]:
        if cls._mock_params is None:
            cls._mock_params = PlatformConfig(test=False).get_optical_params(
                correction=False, exposures=1
            )
        if cls._mock_curve is None:
            cls._mock_curve = PrismCurveLite(
                cls._mock_params, invert_hw_scan_dir=True
            )
        return cls._mock_params, cls._mock_curve

    def _get_scanhead(self, h: int, w: int, px_size_um: float) -> FakeScanhead:
        params, _curve = self._ensure_optical_model()
        key = (h, w, round(px_size_um, 4))
        sh = self._scanhead_cache.get(key)
        if sh is None:
            sh = FakeScanhead(
                params=params,
                facet_offsets_um=Camera.mock_facet_offsets_um,
                rotation_deg=Camera.mock_rotation_deg,
                pixel_size_um=px_size_um,
                sensor_shape=(h, w),
                spot_sigma_scan_um=10.0,
                spot_sigma_orth_um=28.0,
                dark_level=15,
                peak_intensity=230.0,
            )
            self._scanhead_cache[key] = sh
        else:
            sh.facet_offsets_um = {
                int(k): (float(v[0]), float(v[1]))
                for k, v in Camera.mock_facet_offsets_um.items()
            }
            sh.rotation_deg = float(Camera.mock_rotation_deg)
        return sh

    @classmethod
    def _active_events(cls, curve: PrismCurveLite) -> list[tuple[int, int]]:
        if cls.mock_laser_events is not None:
            return cls.mock_laser_events
        if cls.mock_laser_pattern:
            return ulabext.bits_to_events(cls.mock_laser_pattern, curve.total_ticks)
        return []

    def reconfigure(
        self,
        frame_size=None,
        pixel_format=None,
        grab_mode=None,
        fb_count=None,
    ):
        """Simulate hardware reconfigure of OV2640 sensor."""
        if frame_size is not None:
            self.frame_size = frame_size
        if pixel_format is not None:
            self.pixel_format = pixel_format
        if grab_mode is not None:
            self.grab_mode = grab_mode
        if fb_count is not None:
            self.fb_count = fb_count

    def capture(self):
        """Generate dynamic mock image in memory (JPEG preview or SVGA grayscale analysis)."""
        self._capture_count += 1
        w, h = FRAME_SIZE_DIMS[self.frame_size]
        _params, curve = self._ensure_optical_model()

        if self.pixel_format == PixelFormat.GRAYSCALE:
            px_size_um = SVGA_PIXEL_SIZE_UM * (800.0 / w)
            scanhead = self._get_scanhead(h, w, px_size_um)
            events = self._active_events(curve)
            if events:
                frame = scanhead.render_events(
                    events,
                    facet=Camera.mock_active_facet,
                    v_stage_mm=Camera.mock_v_stage_mm,
                )
            else:
                frame = scanhead.render_dark_frame()
            return bytearray(frame.tobytes())

        # Default JPEG mode (OV2640 dark-green background + violet/magenta laser)
        img = Image.new("RGB", (w, h), color=(8, 28, 10))
        draw = ImageDraw.Draw(img)

        for x in range(0, w, 100):
            draw.line([(x, 0), (x, h)], fill=(14, 38, 16), width=1)
        for y in range(0, h, 100):
            draw.line([(0, y), (w, y)], fill=(14, 38, 16), width=1)

        now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        draw.text(
            (15, 15),
            f"MOCK OV2640 SENSOR - FRAME #{self._capture_count}",
            fill=(120, 255, 160),
        )
        draw.text(
            (15, 34),
            f"Time: {now_str}  |  Res: {w}x{h}",
            fill=(180, 210, 180),
        )

        px_size_um = OV2640_PIXEL_SIZE_UM * (1600.0 / w)
        tilt_rad = math.radians(Camera.mock_rotation_deg)
        cos_t = math.cos(tilt_rad)
        sin_t = math.sin(tilt_rad)
        tan_t = math.tan(tilt_rad)
        u_center_mm = curve.lanewidth * 0.5
        inv_px_per_mm = 1000.0 / px_size_um

        corr_tag = (
            " [CORRECTED]"
            if (Camera.mock_apply_correction and Camera.mock_calibrated_facets)
            else " [UNCORRECTED]"
        )

        events = self._active_events(curve)
        if events:
            valid = sorted(
                (int(t), int(v)) for t, v in events if 0 <= int(t) < ulabext.SENTINEL_TICK
            )
            u_dots = []
            for idx, (t_on, inten) in enumerate(valid):
                if inten <= 0 or t_on >= curve.total_ticks:
                    continue
                t_off = valid[idx + 1][0] if idx + 1 < len(valid) else curve.total_ticks
                t_off = min(curve.total_ticks, max(t_on, t_off))
                if t_off > t_on:
                    u_dots.append(curve.tick_to_u(0.5 * (t_on + t_off)))

            if Camera.mock_preview_facet is not None:
                facets_to_draw = [int(Camera.mock_preview_facet) % 4]
                mode_label = (
                    f"PREVIEW DOTS: FACET F{facets_to_draw[0] + 1} "
                    f"({len(u_dots)} dots){corr_tag}"
                )
            else:
                facets_to_draw = [0, 1, 2, 3]
                mode_label = (
                    f"PREVIEW DOTS: ALL 4 FACETS "
                    f"({len(u_dots)} dots x 4){corr_tag}"
                )

            draw.text((15, 55), mode_label, fill=(255, 180, 255))

            for f_idx in facets_to_draw:
                err_scan_um, err_orth_um = Camera.mock_facet_offsets_um[f_idx]
                if (
                    Camera.mock_apply_correction
                    and str(f_idx) in Camera.mock_calibrated_facets
                ):
                    f_cal = Camera.mock_calibrated_facets[str(f_idx)]
                    err_scan_um -= float(f_cal["scan_mean_um"])
                    err_orth_um -= float(f_cal["orth_mean_um"])

                for dot_num, u_mm in enumerate(u_dots):
                    rx = (u_mm - u_center_mm + err_scan_um / 1000.0) * inv_px_per_mm
                    ry = (err_orth_um / 1000.0) * inv_px_per_mm
                    # Display with vertical sweep orientation (matching OV2640 mount)
                    cy = (h * 0.5) + rx * cos_t - ry * sin_t
                    cx = (w * 0.5) - (rx * sin_t + ry * cos_t)
                    draw.ellipse(
                        [(cx - 9, cy - 9), (cx + 9, cy + 9)],
                        fill=(110, 20, 130),
                    )
                    draw.ellipse(
                        [(cx - 6, cy - 6), (cx + 6, cy + 6)],
                        fill=(210, 60, 240),
                    )
                    draw.ellipse(
                        [(cx - 3, cy - 3), (cx + 3, cy + 3)],
                        fill=(255, 235, 255),
                    )
                    if f_idx == facets_to_draw[0]:
                        draw.text(
                            (int(cx + 18), int(cy - 6)),
                            f"dot {dot_num + 1} ({u_mm:.2f} mm)",
                            fill=(220, 180, 245),
                        )
        else:
            draw.text(
                (15, 55),
                f"LIVE CONTINUOUS SCANLINE (F1..F4){corr_tag}",
                fill=(220, 170, 240),
            )
            for f_idx in range(4):
                _, err_orth_um = Camera.mock_facet_offsets_um[f_idx]
                if (
                    Camera.mock_apply_correction
                    and str(f_idx) in Camera.mock_calibrated_facets
                ):
                    f_cal = Camera.mock_calibrated_facets[str(f_idx)]
                    err_orth_um -= float(f_cal["orth_mean_um"])
                d_orth_px = err_orth_um / px_size_um
                x_top = (w * 0.5) - (0.0 - (h * 0.5)) * tan_t + d_orth_px
                x_bot = (w * 0.5) - (float(h) - (h * 0.5)) * tan_t + d_orth_px
                draw.line([(x_top, 0), (x_bot, h)], fill=(120, 25, 145), width=5)
                draw.line([(x_top, 0), (x_bot, h)], fill=(215, 75, 245), width=3)
                draw.line([(x_top, 0), (x_bot, h)], fill=(255, 225, 255), width=1)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality)
        return buf.getvalue()

    def free_buffer(self):
        pass

    def deinit(self):
        pass
