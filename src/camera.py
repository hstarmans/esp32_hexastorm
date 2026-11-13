"""camera.py - Compatibility stub and CPython mock for the MicroPython 'camera' module.

On ESP32 hardware, MicroPython's built-in C-module 'camera' takes precedence.
On host CPython, this provides a dynamic Pillow-based mock image generator for development.
"""

import io
import math
from datetime import datetime
from typing import ClassVar

from PIL import Image, ImageDraw

from control.constants import OV2640_PIXEL_SIZE_UM, SVGA_PIXEL_SIZE_UM


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


def _extract_pattern_clusters(pattern):
    """Group contiguous non-zero bits in a scanline pattern into fractional center indices."""
    clusters = []
    start = -1
    for idx, val in enumerate(pattern):
        if val:
            if start < 0:
                start = idx
        elif start >= 0:
            clusters.append(0.5 * (start + (idx - 1)))
            start = -1
    if start >= 0:
        clusters.append(0.5 * (start + (len(pattern) - 1)))
    return clusters


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

    # Class-level mock hooks for simulated laser projection during tests
    mock_laser_pattern = None
    mock_visible_range = (450, 1150)
    mock_active_facet = 0
    mock_preview_facet = None
    mock_rotation_deg = 1.5
    mock_facet_offsets_um: ClassVar[dict[int, tuple[float, float]]] = {
        0: (14.0, -9.0),
        1: (-44.0, 36.0),
        2: (26.0, -24.0),
        3: (-12.0, 13.0),
    }
    mock_scan_correction_um = 0.0
    mock_orth_correction_um = 0.0
    mock_apply_correction = False
    mock_calibrated_facets: ClassVar[dict] = {}

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
        """Generates dynamic mock image in memory (JPEG or Grayscale)."""
        self._capture_count += 1
        w, h = FRAME_SIZE_DIMS[self.frame_size]

        if self.pixel_format == PixelFormat.GRAYSCALE:
            # Create grayscale buffer with subtle dark background noise (simulating mount)
            buf = bytearray([15] * (w * h))

            # Check if a simulated laser pattern is currently active
            pattern = Camera.mock_laser_pattern
            if pattern:
                scan_len = len(pattern)
                min_vis, max_vis = Camera.mock_visible_range
                if max_vis >= scan_len:
                    min_vis = int(scan_len * 0.25)
                    max_vis = int(scan_len * 0.75)

                clusters = _extract_pattern_clusters(pattern)
                vis_clusters = [c for c in clusters if min_vis <= c <= max_vis]

                if len(clusters) >= 3:
                    px_size_um = SVGA_PIXEL_SIZE_UM * (800.0 / w)
                    facet = int(Camera.mock_active_facet) % 4
                    err_scan_um, err_orth_um = Camera.mock_facet_offsets_um[facet]
                    jitter_s = math.sin(self._capture_count * 1.7 + facet) * 0.35
                    jitter_o = math.cos(self._capture_count * 2.3 + facet) * 0.35
                    dx_px = (
                        err_scan_um + Camera.mock_scan_correction_um + jitter_s
                    ) / px_size_um
                    dy_px = (
                        err_orth_um + Camera.mock_orth_correction_um + jitter_o
                    ) / px_size_um
                    theta = math.radians(Camera.mock_rotation_deg)
                    cos_t = math.cos(theta)
                    sin_t = math.sin(theta)

                    for c_idx in vis_clusters:
                        pos_frac = (c_idx - min_vis) / max(
                            1.0, float(max_vis - min_vis)
                        )
                        rx = 80.0 + pos_frac * (w - 160.0) + dx_px
                        ry = (h * 0.5) + dy_px
                        cx = rx * cos_t - ry * sin_t
                        cy = rx * sin_t + ry * cos_t
                        _draw_gaussian_spot_gray(
                            buf, w, h, cx, cy, sigma=3.2, peak=230.0, radius=10
                        )
                else:
                    for c_idx in vis_clusters:
                        pos_frac = (c_idx - min_vis) / max(
                            1.0, float(max_vis - min_vis)
                        )
                        cx = 30.0 + pos_frac * (w - 60.0)
                        cy = h * 0.5
                        _draw_gaussian_spot_gray(
                            buf, w, h, cx, cy, sigma=3.2, peak=235.0, radius=10
                        )
            return buf

        # Default JPEG mode (realistic OV2640 dark-green background + violet/magenta laser)
        img = Image.new("RGB", (w, h), color=(8, 28, 10))
        draw = ImageDraw.Draw(img)

        # Subtle sensor grid
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
        tan_t = math.tan(tilt_rad)

        corr_tag = (
            " [CORRECTED]"
            if (Camera.mock_apply_correction and Camera.mock_calibrated_facets)
            else " [UNCORRECTED]"
        )

        pattern = Camera.mock_laser_pattern
        if pattern:
            scan_len = len(pattern)
            min_vis, max_vis = Camera.mock_visible_range
            if max_vis >= scan_len:
                min_vis = int(scan_len * 0.25)
                max_vis = int(scan_len * 0.75)

            clusters = _extract_pattern_clusters(pattern)
            vis_clusters = [c for c in clusters if min_vis <= c <= max_vis]

            if Camera.mock_preview_facet is not None:
                facets_to_draw = [int(Camera.mock_preview_facet) % 4]
                mode_label = (
                    f"PREVIEW DOTS: FACET F{facets_to_draw[0] + 1} "
                    f"({len(vis_clusters)} dots){corr_tag}"
                )
            else:
                facets_to_draw = [0, 1, 2, 3]
                mode_label = (
                    f"PREVIEW DOTS: ALL 4 FACETS "
                    f"({len(vis_clusters)} dots x 4){corr_tag}"
                )

            draw.text((15, 55), mode_label, fill=(255, 180, 255))

            for f_idx in facets_to_draw:
                err_scan_um, err_orth_um = Camera.mock_facet_offsets_um[f_idx]
                if (
                    Camera.mock_apply_correction
                    and str(f_idx) in Camera.mock_calibrated_facets
                ):
                    f_cal = Camera.mock_calibrated_facets[str(f_idx)]
                    err_scan_um += float(f_cal["scan_mean_um"])
                    err_orth_um += float(f_cal["orth_mean_um"])

                d_scan_px = err_scan_um / px_size_um
                d_orth_px = err_orth_um / px_size_um

                for dot_num, c_idx in enumerate(vis_clusters):
                    pos_frac = (c_idx - min_vis) / max(1.0, float(max_vis - min_vis))
                    cy = 90.0 + pos_frac * (h - 180.0) + d_scan_px
                    cx = (w * 0.5) - (cy - (h * 0.5)) * tan_t + d_orth_px
                    # Outer magenta glow
                    draw.ellipse(
                        [(cx - 9, cy - 9), (cx + 9, cy + 9)],
                        fill=(110, 20, 130),
                    )
                    draw.ellipse(
                        [(cx - 6, cy - 6), (cx + 6, cy + 6)],
                        fill=(210, 60, 240),
                    )
                    # Bright white-violet core
                    draw.ellipse(
                        [(cx - 3, cy - 3), (cx + 3, cy + 3)],
                        fill=(255, 235, 255),
                    )
                    if f_idx == facets_to_draw[0]:
                        draw.text(
                            (int(cx + 18), int(cy - 6)),
                            f"dot {dot_num + 1} (px {round(c_idx)})",
                            fill=(220, 180, 245),
                        )
        else:
            # Continuous vertical tilted scanlines for facets F1..F4 (matching real OV2640 image)
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
                    err_orth_um += float(f_cal["orth_mean_um"])
                d_orth_px = err_orth_um / px_size_um
                x_top = (w * 0.5) - (0.0 - (h * 0.5)) * tan_t + d_orth_px
                x_bot = (w * 0.5) - (float(h) - (h * 0.5)) * tan_t + d_orth_px
                draw.line(
                    [(x_top, 0), (x_bot, h)],
                    fill=(120, 25, 145),
                    width=5,
                )
                draw.line(
                    [(x_top, 0), (x_bot, h)],
                    fill=(215, 75, 245),
                    width=3,
                )
                draw.line(
                    [(x_top, 0), (x_bot, h)],
                    fill=(255, 225, 255),
                    width=1,
                )

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality)
        return buf.getvalue()

    def free_buffer(self):
        pass

    def deinit(self):
        pass
