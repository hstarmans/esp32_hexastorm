"""End-to-end calibration loop test executed under real MicroPython + ulab.

Run directly via MicroPython (with ulab compiled in):
    micropython -X heapsize=8M src/tests/mpy_test_calibration.py
"""

import asyncio
import deflate
import gc
import json
import math
import os
import sys

try:
    from typing import TYPE_CHECKING
except ImportError:
    TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Any

# Verify real MicroPython + built-in C ulab + _vector_core BEFORE src/ is added to sys.path,
# so host shims (src/ulab.py, src/deflate.py) cannot shadow native modules.
assert sys.implementation.name == "micropython", (
    f"Expected micropython runtime, got {sys.implementation.name}"
)
import _vector_core  # noqa: E402  # pyrefly: ignore[missing-import]
from ulab import numpy as np  # noqa: E402

assert isinstance(np.uint8, int), "Expected real ulab C module (int dtype constants)"
assert hasattr(_vector_core, "slice_lane_spi"), "Expected native _vector_core C module"
assert hasattr(deflate, "DeflateIO") and hasattr(deflate, "ZLIB")
NP_FLOAT = getattr(np, "float", float)


def _env(name: str, default: str = "") -> str:
    getenv = getattr(os, "getenv", None)
    if callable(getenv):
        val = getenv(name)
        if val:
            return str(val)
    environ = getattr(os, "environ", None)
    if environ is not None:
        val = environ.get(name)
        if val:
            return str(val)
    return default


def _dirname(path: str) -> str:
    idx = path.rfind("/")
    if idx <= 0:
        return "."
    return path[:idx]


def _path_exists(path: str) -> bool:
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _is_dir(path: str) -> bool:
    try:
        mode = os.stat(path)[0]
        return (mode & 0o170000) == 0o040000
    except OSError:
        return False


def _makedirs(path: str) -> None:
    if not path or path == "/":
        return
    parts = path.split("/")
    cur = "/" if path.startswith("/") else ""
    for part in parts:
        if not part or part == ".":
            continue
        cur = (
            f"{cur}/{part}"
            if cur and cur != "/"
            else (f"/{part}" if cur == "/" else part)
        )
        if not _path_exists(cur):
            os.mkdir(cur)


def _rmtree(path: str) -> None:
    if not _path_exists(path):
        return
    if not _is_dir(path):
        os.remove(path)
        return
    for entry in os.listdir(path):
        child = f"{path}/{entry}"
        if _is_dir(child):
            _rmtree(child)
        else:
            os.remove(child)
    os.rmdir(path)


def _configure_paths() -> tuple[str, str]:
    workspace = _env("MPY_WORKSPACE")
    if not workspace:
        # Derive repo root from .../src/tests/mpy_test_calibration.py
        workspace = _dirname(_dirname(_dirname(__file__)))
        if workspace == ".":
            workspace = os.getcwd()

    hexastorm_src = _env("MPY_HEXASTORM_SRC")
    if not hexastorm_src:
        hexastorm_src = f"{workspace}/deps/hexastorm/src"

    stdlib_root = _env("MPY_STDLIB_ROOT")
    extra_paths = []
    if stdlib_root:
        extra_paths.append(f"{stdlib_root}/logging")
        extra_paths.append(f"{stdlib_root}/unittest")
    extra_paths.extend(
        [
            hexastorm_src,
            f"{workspace}/vendor",
            f"{workspace}/src",
        ]
    )
    for p in extra_paths:
        if p and p not in sys.path:
            sys.path.append(p)
    return workspace, hexastorm_src


WORKSPACE_ROOT, HEXASTORM_SRC_ROOT = _configure_paths()

# Prepare isolated working directory so factory config.json / mock files in workspace stay clean
TMP_ROOT = "/tmp/mpy_calib_test"
_rmtree(TMP_ROOT)
_makedirs(f"{TMP_ROOT}/src/root/sd/jobs")
_makedirs(f"{TMP_ROOT}/sd/jobs")

with open(f"{WORKSPACE_ROOT}/src/root/config.json", "rb") as _f_src:
    _factory_bytes = _f_src.read()
with open(f"{TMP_ROOT}/config.json", "wb") as _f_dst:
    _f_dst.write(_factory_bytes)
with open(f"{TMP_ROOT}/src/root/config.json", "wb") as _f_dst2:
    _f_dst2.write(_factory_bytes)

os.chdir(TMP_ROOT)

# Switch control.constants into real ESP32 mode BEFORE importing camera / calibration / laserhead
from control import constants  # noqa: E402

constants.ESP32 = True
constants.CONFIG_FILE = "config.json"
constants.CONFIG_OLD_FILE = "config_old.json"
constants.NVS_FILE = "nvs_mock.json"
constants.NVS_STORE.mock_file = "nvs_mock.json"
constants.CONFIG.clear()
constants.CONFIG.update(constants.load_config())

from hexastorm.calibration.core import PrismCurveLite  # noqa: E402
from hexastorm.calibration.vector_lite import (  # noqa: E402
    HAS_VECTOR_CORE,
    decode_spi_scanlines,
    slice_polygons_to_spi,
)
from hexastorm.config import PlatformConfig  # noqa: E402
from hexastorm.fpga_host.tools import find_shift  # noqa: E402
from hexastorm.ulabext import SENTINEL_TICK  # noqa: E402


class UlabFakeScanhead:
    """MicroPython + ulab optical forward simulator rendering 24 MHz events into uint8 DMA buffer."""

    def __init__(
        self,
        facet_offsets_um: "dict[int, tuple[float, float]] | None" = None,
        rotation_deg: float = 1.5,
        pixel_size_um: float = 4.4,
        sensor_shape: "tuple[int, int]" = (600, 800),
        spot_sigma_scan_um: float = 10.0,
        spot_sigma_orth_um: float = 28.0,
        dark_level: int = 15,
        peak_intensity: float = 230.0,
        facet_shift: int = 2,
    ):
        self.params = PlatformConfig(test=False).get_optical_params(
            correction=False, exposures=1
        )
        self.curve = PrismCurveLite(self.params, invert_hw_scan_dir=True)
        default_offsets = {
            0: (14.0, -9.0),
            1: (-44.0, 36.0),
            2: (26.0, -24.0),
            3: (-12.0, 13.0),
        }
        src = default_offsets if facet_offsets_um is None else facet_offsets_um
        self.facet_offsets_um: "dict[int, tuple[float, float]]" = {
            k: (float(v[0]), float(v[1])) for k, v in src.items()
        }
        self.rotation_deg = float(rotation_deg)
        self.pixel_size_um = float(pixel_size_um)
        self.sensor_shape = (sensor_shape[0], sensor_shape[1])
        self.u_center_mm = self.curve.lanewidth * 0.5
        self.v_center_mm = 0.0
        self.spot_sigma_scan_um = float(spot_sigma_scan_um)
        self.spot_sigma_orth_um = float(spot_sigma_orth_um)
        self.dark_level = dark_level
        self.peak_intensity = float(peak_intensity)
        self.facet_shift = facet_shift

        h, w = self.sensor_shape
        self._dark_bytes = bytes([self.dark_level]) * (h * w)
        self.active_events: "list[tuple[int, int]]" = []
        self.active_physical_facet = 0

    def set_projection(
        self, events: "list[tuple[int, int]] | None", facet: int = 0
    ) -> None:
        self.active_events = list(events) if events else []
        self.active_physical_facet = facet

    def clear_projection(self) -> None:
        self.active_events = []

    def render_into(self, buf: bytearray) -> None:
        h, w = self.sensor_shape
        buf[:] = self._dark_bytes
        if not self.active_events:
            return

        n_facets = int(self.params["facets"])
        f_idx = (self.active_physical_facet - self.facet_shift) % n_facets
        err_scan_um, err_orth_um = self.facet_offsets_um.get(f_idx, (0.0, 0.0))
        err_scan_mm = err_scan_um / 1000.0
        err_orth_mm = err_orth_um / 1000.0

        total_ticks = self.curve.total_ticks
        valid = sorted(
            (t, max(0, min(255, v)))
            for t, v in self.active_events
            if 0 <= t < SENTINEL_TICK
        )
        segments: "list[tuple[int, int, int]]" = []
        for idx, (t_on, intensity) in enumerate(valid):
            if intensity <= 0 or t_on >= total_ticks:
                continue
            t_off = valid[idx + 1][0] if idx + 1 < len(valid) else total_ticks
            t_off = min(total_ticks, max(t_on, t_off))
            if t_off > t_on:
                segments.append((t_on, t_off, intensity))

        if not segments:
            return

        canvas = np.frombuffer(buf, dtype=np.uint8).reshape((h, w))

        sig_scan_px = max(0.5, self.spot_sigma_scan_um / self.pixel_size_um)
        sig_orth_px = max(0.5, self.spot_sigma_orth_um / self.pixel_size_um)
        inv_s2 = 1.0 / (sig_scan_px * sig_scan_px)
        inv_o2 = 1.0 / (sig_orth_px * sig_orth_px)

        theta = math.radians(self.rotation_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        inv_px_per_mm = 1000.0 / self.pixel_size_um

        r_s = 3.5 * sig_scan_px
        r_o = 3.5 * sig_orth_px
        half_x = math.ceil(abs(cos_t) * r_s + abs(sin_t) * r_o)
        half_y = math.ceil(abs(sin_t) * r_s + abs(cos_t) * r_o)

        ry_rel = (err_orth_mm - self.v_center_mm) * inv_px_per_mm
        step_u_mm = max(0.002, 0.5 * (self.spot_sigma_scan_um / 1000.0))
        half_w_px = 0.5 * w
        half_h_px = 0.5 * h

        for t_on, t_off, intensity in segments:
            ua = self.curve.tick_to_u(t_on) + err_scan_mm
            ub = self.curve.tick_to_u(t_off) + err_scan_mm
            if ub < ua:
                ua, ub = ub, ua
            span_u = ub - ua
            n_sub = max(1, math.ceil(span_u / step_u_mm))
            amp = self.peak_intensity * (intensity / 255.0)

            for k in range(n_sub):
                u_s = ua + ((k + 0.5) / n_sub) * span_u if span_u > 0 else ua
                rx_rel = (u_s - self.u_center_mm) * inv_px_per_mm
                cx = half_w_px + rx_rel * cos_t - ry_rel * sin_t
                cy = half_h_px + rx_rel * sin_t + ry_rel * cos_t

                x0 = max(0, math.floor(cx - half_x))
                x1 = min(w, math.ceil(cx + half_x) + 1)
                y0 = max(0, math.floor(cy - half_y))
                y1 = min(h, math.ceil(cy + half_y) + 1)
                if x1 <= x0 or y1 <= y0:
                    continue

                nx = x1 - x0
                ny = y1 - y0
                xs = (np.arange(x0, x1, 1, dtype=NP_FLOAT) - cx).reshape((1, nx))
                ys = (np.arange(y0, y1, 1, dtype=NP_FLOAT) - cy).reshape((ny, 1))
                u_p = xs * cos_t + ys * sin_t
                v_p = -xs * sin_t + ys * cos_t
                patch_f = amp * np.exp(
                    -0.5 * ((u_p * u_p) * inv_s2 + (v_p * v_p) * inv_o2)
                )
                patch_u8 = np.array(
                    np.minimum(255.0, patch_f + float(self.dark_level) + 0.5),
                    dtype=np.uint8,
                )
                canvas[y0:y1, x0:x1] = np.maximum(canvas[y0:y1, x0:x1], patch_u8)


_SCANHEAD = UlabFakeScanhead(facet_shift=2)


class _MockFrameSize:
    QQVGA = 1
    QVGA = 6
    VGA = 10
    SVGA = 11
    HD = 13
    SXGA = 14
    UXGA = 15


class _MockPixelFormat:
    RGB565 = 0
    YUV422 = 1
    GRAYSCALE = 3
    JPEG = 4
    RAW = 6


class _MockGrabMode:
    WHEN_EMPTY = 0
    LATEST = 1


class _MockGainCeiling:
    X2 = 0


_DIMS_BY_SIZE = {
    _MockFrameSize.QQVGA: (160, 120),
    _MockFrameSize.QVGA: (320, 240),
    _MockFrameSize.VGA: (640, 480),
    _MockFrameSize.SVGA: (800, 600),
    _MockFrameSize.HD: (1280, 720),
    _MockFrameSize.SXGA: (1280, 1024),
    _MockFrameSize.UXGA: (1600, 1200),
}


class _MockCamera:
    """ESP32-like Camera stub: NO mock_* class attributes and returns DMA memoryview."""

    def __init__(self, *args, **kwargs):
        self.frame_size = kwargs.get("frame_size", _MockFrameSize.UXGA)
        self.pixel_format = kwargs.get("pixel_format", _MockPixelFormat.JPEG)
        self.jpeg_quality = kwargs.get("jpeg_quality", 85)
        self.fb_count = kwargs.get("fb_count", 1)
        self.grab_mode = kwargs.get("grab_mode", _MockGrabMode.LATEST)
        self.exposure_ctrl = False
        self.aec2 = False
        self.gain_ctrl = False
        self.agc_gain = 0
        self.whitebal = False
        self.awb_gain = False
        self.bpc = False
        self.wpc = False
        self.aec_value = 1200
        self.discard_count = 0
        self._frame_buf = bytearray(800 * 600)

    def reconfigure(
        self, frame_size=None, pixel_format=None, grab_mode=None, fb_count=None
    ):
        if frame_size is not None:
            self.frame_size = frame_size
        if pixel_format is not None:
            self.pixel_format = pixel_format
        if grab_mode is not None:
            self.grab_mode = grab_mode
        if fb_count is not None:
            self.fb_count = fb_count

    def capture(self):
        if self.pixel_format == _MockPixelFormat.GRAYSCALE:
            w, h = _DIMS_BY_SIZE[self.frame_size]
            if len(self._frame_buf) != w * h:
                self._frame_buf = bytearray(w * h)
            _SCANHEAD.render_into(self._frame_buf)
            return memoryview(self._frame_buf)
        return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

    def free_buffer(self):
        # Clobber first byte to verify capture_grayscale_array() made an independent copy
        if self.pixel_format == _MockPixelFormat.GRAYSCALE and len(self._frame_buf) > 0:
            self._frame_buf[0] = 0xAA

    def deinit(self):
        pass


class _MockCameraModule:
    """ESP32-accurate camera module without FRAME_SIZE_DIMS."""

    Camera = _MockCamera
    FrameSize = _MockFrameSize
    PixelFormat = _MockPixelFormat
    GrabMode = _MockGrabMode
    GainCeiling = _MockGainCeiling


_cam_mod: "Any" = _MockCameraModule()
sys.modules["camera"] = _cam_mod

# Now import the real control modules under MicroPython + ulab
from control import calibration  # noqa: E402
from control.camera import FrameSize, PixelFormat, camera  # noqa: E402
from control.laserhead.base import BaseLaserhead  # noqa: E402


class MockMicroPythonLaserhead(BaseLaserhead):
    """MicroPython mock inheriting BaseLaserhead and wiring 24 MHz events to UlabFakeScanhead."""

    def __init__(self, scanhead: UlabFakeScanhead):
        super().__init__()
        self._scanhead = scanhead
        self.facet_shift = scanhead.facet_shift
        self.logical_facet_means = [5.002, 4.996, 5.005, 4.997]
        constants.CONFIG["laserhead"]["facetmeans"] = list(self.logical_facet_means)
        # Physical periods rotated right by facet_shift=2 relative to logical reference
        self.cur_facet_means = [5.005, 4.997, 5.002, 4.996]
        self.sync_calls: "list[bool]" = []
        self.comp_calls: "list[tuple]" = []
        self.write_events_calls = 0
        self.wait_fifo_calls = 0
        self.packets_checked = 0

    async def remap(self, facet_id=0):
        num_facets = int(self.cfg.laser_timing["facets"])
        ref_means = constants.CONFIG["laserhead"]["facetmeans"]
        shift, _corr, _rmse = find_shift(self.cur_facet_means, ref_means)
        return int((facet_id + shift) % num_facets)

    async def synchronize(self, value=True) -> bool:
        self.sync_calls.append(bool(value))
        return True

    async def enable_comp(
        self,
        laser0=None,
        laser1=None,
        polygon=None,
        synchronize=None,
        singlefacet=None,
    ):
        await super().enable_comp(
            laser0=laser0,
            laser1=laser1,
            polygon=polygon,
            synchronize=synchronize,
            singlefacet=singlefacet,
        )
        self.comp_calls.append((laser0, laser1, polygon, synchronize, singlefacet))
        if laser0 is False and laser1 is False:
            self._scanhead.clear_projection()

    async def write_events(
        self, events, steps_line=1, direction=0, repetitions=1, facet=None
    ):
        self.write_events_calls += 1
        if events:
            byte_lst = self.events_to_byte_list(
                events, steps_line=steps_line, direction=direction
            )
            cmd_lst = self.byte_to_cmd_list(byte_lst)
            assert len(cmd_lst) >= 1
            packed = bytearray()
            for pkt in cmd_lst:
                packed.extend(pkt)
            assert self._count_scanlines_in_command(packed) == 1
            self.packets_checked += 1
            self._scanhead.set_projection(events, 0 if facet is None else int(facet))
        else:
            self._scanhead.clear_projection()
        await asyncio.sleep(0)

    async def wait_fifo_empty(
        self, poll_interval=0.01, check_sensors=False, timeout=5.0
    ):
        self.wait_fifo_calls += 1

    async def gotopoint(
        self,
        position,
        speed=None,
        absolute=True,
        check_sensors=False,
        workspace=False,
        validate_limits=True,
    ):
        if validate_limits:
            self._validate_target_position(
                position, absolute=absolute, workspace=workspace
            )
        self._update_coordinates(position, absolute, workspace)
        await self.notify_listeners()


def _assert_no_mock_camera_attrs() -> None:
    assert not hasattr(sys.modules["camera"], "FRAME_SIZE_DIMS")
    for attr in (
        "mock_laser_events",
        "mock_laser_pattern",
        "mock_active_facet",
        "mock_v_stage_mm",
        "mock_preview_facet",
        "mock_calibrated_facets",
        "mock_apply_correction",
    ):
        assert not hasattr(_MockCamera, attr), f"Unexpected Camera.{attr} mutation"


async def run_all_tests() -> None:
    _assert_no_mock_camera_attrs()

    # 1. Verify PlatformConfig photodiode scan correction in MicroPython + ulab
    cfg = PlatformConfig(test=False)
    pd_corr = cfg.compute_photodiode_scan_corrections([5.01, 4.98, 5.02, 4.99])
    assert len(pd_corr) == 4
    assert abs(pd_corr["1"]["scan"]) > 0.0

    # 2. Verify BaseLaserhead coordinates, state, errors, PWM, and SPI encoding
    lh = MockMicroPythonLaserhead(_SCANHEAD)
    await lh.gotopoint([10.0, 5.0, 1.0], absolute=True, workspace=False)
    assert abs(lh.mpos[0] - 10.0) < 1e-6 and abs(lh.mpos[1] - 5.0) < 1e-6
    await lh.set_workspace_zero([1, 1, 0])
    assert abs(lh.wpos[0]) < 1e-6 and abs(lh.wpos[1]) < 1e-6
    await lh.set_error("test error")
    assert lh.state["error_message"] == "test error"
    await lh.clear_error()
    assert lh.state["error_message"] is None  # pyrefly: ignore[unnecessary-comparison]
    await lh.set_spindle(128)
    await lh.set_fan(200)
    assert lh.state["components"]["spindle"] == 128
    assert lh.state["components"]["fan"] == 200

    bits_len = int(lh.cfg.get_optical_params()["bitsinscanline"])
    bit_lst = [1, 0, 1] + [0] * (bits_len - 3)
    cmd_lst = lh.byte_to_cmd_list(
        lh.bit_to_byte_list(bit_lst, steps_line=0, direction=1)
    )
    assert len(cmd_lst) == 3

    # 3. Verify CameraController uint8 DMA copy & mode restoration
    assert camera.current_frame_size == FrameSize.UXGA
    assert camera.current_pixel_format == PixelFormat.JPEG
    with camera.analysis_mode(
        frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
    ):
        _SCANHEAD.clear_projection()
        arr = camera.capture_grayscale_array()
        assert arr.shape == (600, 800)
        assert arr.dtype == np.uint8, f"Expected uint8, got {arr.dtype}"
        assert int(arr[0, 0]) == 15, (
            f"DMA buffer was not copied safely: {int(arr[0, 0])}"
        )
    assert camera.current_frame_size == FrameSize.UXGA
    assert camera.current_pixel_format == PixelFormat.JPEG

    # 4. Run fast scan visibility in real MicroPython + ulab
    vis = await calibration.run_scan_visibility(lh, camera, facet=0, step=20, fast=True)
    assert vis["status"] == "success"
    assert vis["fast"] is True
    assert vis["timing"]["captures"] < 22, (
        f"Expected < 22 fast visibility captures, got {vis['timing']}"
    )
    bounds = vis["visible_pixels"]
    assert isinstance(bounds, list) and len(bounds) == 2
    assert 0 < bounds[0] < bounds[1] < 560, f"Unexpected bounds: {bounds}"
    assert 30.0 < vis["percentage"] < 80.0
    assert any(c[3] is True for c in lh.comp_calls)
    assert True in lh.sync_calls

    # 4b. Verify _vector_core C user module produces valid SPI packets in MicroPython
    assert HAS_VECTOR_CORE is True
    vec_params = cfg.get_optical_params(correction=False, exposures=1)
    raw_spi, active_lines = slice_polygons_to_spi(
        polygons=[[(2.0, 0.1), (2.2, 0.1), (2.2, 0.2), (2.0, 0.2)]],
        params=vec_params,
        u_size_mm=float(vec_params["lanewidth"]),
        v_size_mm=0.5,
        steps_per_line=0.5,
        facets_inlane=10,
        y_shift_override=0.05,
        include_stage_drift=False,
    )
    assert active_lines > 0 and len(raw_spi) % 9 == 0
    decoded_rows = decode_spi_scanlines(raw_spi, num_facets=4, skip_empty=True)
    assert len(decoded_rows) == active_lines and len(decoded_rows[0][2]) == 2

    # 5. Run 1D multi-facet calibration and verify recovered ground-truth offsets
    cal = await calibration.run_facet_calibration(lh, camera, iterations=2, num_dots=6)
    assert cal["status"] == "success"
    facets = cal["facets"]
    assert len(facets) == 4
    true_vals = list(_SCANHEAD.facet_offsets_um.values())
    med_s = sorted(v[0] for v in true_vals)
    med_o = sorted(v[1] for v in true_vals)
    ref_s = 0.5 * (med_s[1] + med_s[2])
    ref_o = 0.5 * (med_o[1] + med_o[2])

    for f_idx in range(4):
        f_cfg = facets[str(f_idx)]
        exp_s = _SCANHEAD.facet_offsets_um[f_idx][0] - ref_s
        exp_o = _SCANHEAD.facet_offsets_um[f_idx][1] - ref_o
        assert abs(f_cfg["scan_mean_um"] - exp_s) <= 3.5, (
            f"Facet {f_idx} scan_mean_um={f_cfg['scan_mean_um']} vs exp={exp_s}"
        )
        assert abs(f_cfg["orth_mean_um"] - exp_o) <= 3.5, (
            f"Facet {f_idx} orth_mean_um={f_cfg['orth_mean_um']} vs exp={exp_o}"
        )
        assert abs(f_cfg["rotation_deg"] - 1.5) <= 0.2
        assert abs(f_cfg["period_ms"] - lh.logical_facet_means[f_idx]) < 1e-6

    # Verify config.json persisted cleanly by dump_pretty_json
    with open("config.json", "r") as f_cfg_in:
        saved_cfg = json.load(f_cfg_in)
    assert saved_cfg["calibration"]["visible_pixels"] == bounds
    assert len(saved_cfg["calibration"]["facets"]) == 4

    # 6. Run fast closed-loop 2D Verified Calibration (analytical slicer, exposures_2d=2)
    lh.packets_checked = 0
    report = await calibration.run_pattern_error(
        lh,
        camera,
        num_dots=6,
        calibrate_first=True,
        exposures_2d=2,
        verify_before_2d=False,
    )
    assert report["status"] == "success"
    assert report["passed"] is True, f"2D calibration failed: {report}"
    assert report["exposures_2d"] == 2
    assert report["timing"]["captures"] <= 25, (
        f"Expected <= 25 captures in fast mode, got {report['timing']}"
    )
    assert report["max_error_before_um"] > 30.0
    assert report["max_error_after_um"] <= 10.0
    assert report["max_error_after_um"] < report["max_error_before_um"]
    assert len(report["facets"]) == 4
    assert lh.packets_checked >= 15
    assert calibration.get_calibration_status()["state"] == "done"

    # 6b. Run closed-loop 2D Verified Calibration through native C _vector_core slicer
    report_vec = await calibration.run_pattern_error(
        lh,
        camera,
        num_dots=6,
        calibrate_first=True,
        exposures_2d=2,
        verify_before_2d=False,
        slicer="vector_core",
    )
    assert report_vec["status"] == "success"
    assert report_vec["slicer"] == "vector_core"
    assert report_vec["passed"] is True, (
        f"vector_core 2D calibration failed: {report_vec}"
    )
    assert report_vec["max_error_after_um"] <= 10.0

    # 7. Modify Facet 0 custom offsets and re-verify (calibrate_first=False)
    custom_facets = [
        {
            "facet": f["facet"],
            "corr_scan_um": f["corr_scan_um"] + (15.0 if f["facet"] == 0 else 0.0),
            "corr_orth_um": f["corr_orth_um"] + (20.0 if f["facet"] == 0 else 0.0),
        }
        for f in report["facets"]
    ]
    upd = calibration.update_custom_facets(
        facets_override=custom_facets, apply_correction=True
    )
    assert upd["status"] == "success"
    assert upd["apply_correction"] is True

    rep_custom = await calibration.run_pattern_error(
        lh,
        camera,
        num_dots=6,
        calibrate_first=False,
        exposures_2d=2,
        verify_before_2d=False,
    )
    assert rep_custom["status"] == "success"
    assert abs(rep_custom["facets"][0]["after_scan_um"]) > 5.0
    assert abs(rep_custom["facets"][0]["after_orth_um"]) > 5.0

    # 7b. Verify graceful mid-run cancellation restores camera mode & status
    cancel_task = asyncio.create_task(
        calibration.run_pattern_error(lh, camera, num_dots=6, calibrate_first=True)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calibration.is_calibration_running() is True
    assert calibration.request_cancel_calibration() is True
    cancelled_raised = False
    try:
        await cancel_task
    except calibration.CalibrationCancelled:
        cancelled_raised = True
    assert cancelled_raised, "Expected CalibrationCancelled on mid-run cancel"
    assert calibration.is_calibration_running() is False
    assert calibration.get_calibration_status()["state"] == "cancelled"
    assert camera.current_frame_size == FrameSize.UXGA
    assert camera.current_pixel_format == PixelFormat.JPEG

    # 8. Exercise Preview Dots + JPEG snapshot + background feeder cleanup
    assert calibration.set_bounds_preview(lh, enabled=True, facet=0) is True
    await asyncio.sleep(0)
    jpeg0 = await calibration.capture_jpeg_with_preview(lh, camera)
    assert jpeg0 and jpeg0.startswith(b"\xff\xd8") and jpeg0.endswith(b"\xff\xd9")

    assert calibration.set_bounds_preview(lh, enabled=True, facet="all") is True
    assert calibration.get_preview_facet() is None
    await asyncio.sleep(0)
    jpeg_all = await calibration.capture_jpeg_with_preview(lh, camera)
    assert (
        jpeg_all and jpeg_all.startswith(b"\xff\xd8") and jpeg_all.endswith(b"\xff\xd9")
    )

    assert calibration.set_bounds_preview(lh, enabled=False) is False
    await asyncio.sleep(0)

    _assert_no_mock_camera_attrs()

    gc.collect()
    mem_free = getattr(gc, "mem_free", lambda: 0)()
    mem_alloc = getattr(gc, "mem_alloc", lambda: 0)()
    print(
        f"MICROPYTHON CALIBRATION SUITE PASSED | "
        f"before={report['max_error_before_um']} um -> after={report['max_error_after_um']} um "
        f"(vec={report_vec['max_error_after_um']} um) | "
        f"captures={report['timing']['captures']} | "
        f"packets={lh.packets_checked} | mem_alloc={mem_alloc} | mem_free={mem_free}"
    )


def main() -> None:
    try:
        asyncio.run(run_all_tests())
    finally:
        os.chdir("/")
        _rmtree(TMP_ROOT)


if __name__ == "__main__":
    main()
