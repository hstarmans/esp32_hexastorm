import asyncio
import importlib.abc
import importlib.util
import json
from typing import Any

from control.camera import camera
from control.webapp import app
from microdot.test_client import TestClient


def _json(res) -> dict[str, Any]:
    assert isinstance(res.json, dict)
    return res.json


def _body(res) -> bytes:
    assert res.body is not None
    return res.body


def test_camera_pins_loaded_from_platform_config():
    """Verify camera pins are read directly from PlatformConfig.esp32_cfg."""
    assert camera.cam_cfg["d0"] == 12
    assert camera.cam_cfg["vsync"] == 41
    assert camera.cam_cfg["href"] == 40
    assert camera.cam_cfg["xclk"] == 39
    assert camera.cam_cfg["xclk_freq"] == 10000000
    assert camera.xclk_freq == 10000000
    assert camera.cam_cfg["reset"] == 42
    assert camera.i2c_cfg["sda"] == 18
    assert camera.i2c_cfg["scl"] == 17
    assert camera.data_pins == [12, 14, 21, 13, 11, 10, 9, 8]
    assert "settings" in camera.cam_cfg
    assert isinstance(camera.settings, dict)
    assert camera.settings["exposure_ms"] in (50, 200)
    assert camera.cam is not None


def test_camera_mock_capture():
    """Verify mock capture produces a valid JPEG."""
    data = camera.capture()
    assert data is not None
    assert len(data) > 0
    # Must start with JPEG Start Of Image marker (0xFFD8)
    assert data.startswith(b"\xff\xd8")
    # Must end with JPEG End Of Image marker (0xFFD9)
    assert data.endswith(b"\xff\xd9")


def test_camera_snapshot_endpoint():
    """Verify webserver /api/camera/snapshot endpoint with authorization."""

    async def run():
        client = TestClient(app)

        # Unauthorized access check (API routes return 401)
        res_unauth = await client.get("/api/camera/snapshot")
        assert res_unauth.status_code == 401

        # Authorize session
        await client.post(
            "/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="password=hex",
        )

        # Authorized snapshot check
        res = await client.get("/api/camera/snapshot")
        assert res.status_code == 200
        assert res.headers.get("Content-Type") == "image/jpeg"
        body = _body(res)
        assert body.startswith(b"\xff\xd8")
        assert body.endswith(b"\xff\xd9")

    asyncio.run(run())


def test_calibration_endpoints():
    """Verify camera calibration API: save position, move to camera, alignment beam."""

    async def run():
        client = TestClient(app)

        # Authorize session
        await client.post(
            "/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="password=hex",
        )

        # 1. Save camera calibration position
        res = await client.post(
            "/api/calibration/save_camera_pos",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"position": [105.5, 5.2, 12.3]}),
        )
        assert res.status_code == 200
        data = _json(res)
        assert data["calibration"]["camera_position"] == [105.5, 5.2, 12.3]

        # 2. Move to camera position
        res = await client.post("/api/calibration/move_to_camera")
        assert res.status_code == 200

        # 3. Set camera exposure in ms
        res = await client.post(
            "/api/camera/settings",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"exposure_ms": 50}),
        )
        assert res.status_code == 200
        data = _json(res)
        assert data["calibration"]["camera_exposure_ms"] == 50
        assert camera.exposure_ms == 50

    asyncio.run(run())


def test_camera_error_if_cam_is_none():
    """Verify set_exposure_ms raises RuntimeError if cam is None."""
    import pytest

    old_cam = camera.cam
    try:
        camera.cam = None
        with pytest.raises(RuntimeError, match="Camera is None"):
            camera.set_exposure_ms(100)
    finally:
        camera.cam = old_cam


def test_camera_analysis_mode_and_grayscale():
    """Verify analysis_mode temporarily switches to SVGA GRAYSCALE and restores UXGA JPEG."""
    from camera import FrameSize, PixelFormat

    assert camera.current_frame_size == FrameSize.UXGA
    assert camera.current_pixel_format == PixelFormat.JPEG

    with camera.analysis_mode(
        frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
    ):
        assert camera.current_frame_size == FrameSize.SVGA
        assert camera.current_pixel_format == PixelFormat.GRAYSCALE
        arr = camera.capture_grayscale_array()
        assert arr.shape == (600, 800)
        assert arr.dtype.name in ("uint8",)

    # After exiting analysis context, camera must be restored to UXGA JPEG
    assert camera.current_frame_size == FrameSize.UXGA
    assert camera.current_pixel_format == PixelFormat.JPEG


def test_scan_visibility_and_bounds_endpoints():
    """Verify scan visibility and show_bounds API endpoints and state updates."""

    async def run():
        client = TestClient(app)

        # Authorize session
        await client.post(
            "/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="password=hex",
        )

        # 1. Run scan_visibility endpoint
        res = await client.post(
            "/api/calibration/scan_visibility",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"facet": 0, "step": 20}),
        )
        assert res.status_code == 200
        data = _json(res)
        assert data["status"] == "success"
        assert "visible_pixels" in data
        bounds = data["visible_pixels"]
        assert isinstance(bounds, list) and len(bounds) == 2
        assert 0 < bounds[0] < bounds[1] < 560
        assert 30.0 < data["percentage"] < 80.0

        # 2. Toggle show_bounds ON
        res_bounds_on = await client.post(
            "/api/calibration/show_bounds",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"enabled": True, "facet": 0}),
        )
        assert res_bounds_on.status_code == 200
        bounds_on_data = _json(res_bounds_on)
        assert bounds_on_data["bounds_active"] is True
        assert bounds_on_data["visible_pixels"] == bounds

        # 3. Snapshot while bounds are active should capture successfully
        snap_res = await client.get("/api/camera/snapshot")
        assert snap_res.status_code == 200
        assert snap_res.headers.get("Content-Type") == "image/jpeg"

        # 4. Toggle show_bounds OFF
        res_bounds_off = await client.post(
            "/api/calibration/show_bounds",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"enabled": False, "facet": 0}),
        )
        assert res_bounds_off.status_code == 200
        assert _json(res_bounds_off)["bounds_active"] is False

    asyncio.run(run())


def test_get_dots_ulab_subpixel_accuracy():
    """Verify get_dots_ulab detects sub-pixel 2D Gaussian centroids within < 0.25 px."""
    import numpy as np

    from camera import _draw_gaussian_spot_gray
    from control.calibration import get_dots_ulab

    w, h = 800, 600
    dark_buf = bytearray([15] * (w * h))
    img_buf = bytearray([15] * (w * h))

    ground_truth = [
        (110.25, 294.75),
        (220.60, 296.40),
        (335.15, 298.10),
        (450.80, 300.30),
        (565.40, 302.05),
        (680.90, 303.85),
    ]
    for cx, cy in ground_truth:
        _draw_gaussian_spot_gray(
            img_buf, w, h, cx, cy, sigma=3.2, peak=230.0, radius=10
        )

    dark_arr = np.frombuffer(dark_buf, dtype=np.uint8).reshape((h, w))
    img_arr = np.frombuffer(img_buf, dtype=np.uint8).reshape((h, w))

    dots, metrics = get_dots_ulab(img_arr, dark_frame=dark_arr, pixelsize=4.4)
    assert dots.shape == (6, 2)
    assert metrics["num_dots"] == 6
    assert metrics["mean_spot_size_um"] > 20.0
    assert 0.0 <= metrics["mean_eccentricity"] < 0.25

    for idx, (gx, gy) in enumerate(ground_truth):
        dx = abs(float(dots[idx, 0]) - gx)
        dy = abs(float(dots[idx, 1]) - gy)
        assert dx < 0.20, f"Dot {idx} X error {dx:.4f} px >= 0.20 px"
        assert dy < 0.20, f"Dot {idx} Y error {dy:.4f} px >= 0.20 px"


def test_rotation_and_facet_shifts_math():
    """Verify pure-ulab compute_angle_ulab, rotate_dots_ulab, and calculate_facet_shifts_ulab."""
    import math

    import numpy as np

    from control.calibration import (
        calculate_facet_shifts_ulab,
        compute_angle_ulab,
        rotate_dots_ulab,
    )

    true_angle = 2.35
    theta = math.radians(true_angle)
    c, s = math.cos(theta), math.sin(theta)
    px_size = 4.4

    # Zero-median facet offsets in um
    true_offsets_um = [
        (0.0, 0.0),
        (-35.0, 25.0),
        (20.0, -20.0),
        (0.0, 0.0),
    ]
    base_xs = [120.0, 240.0, 360.0, 480.0, 600.0]
    base_y = 300.0

    rect_dots_list = []
    for s_um, o_um in true_offsets_um:
        pts = []
        for bx in base_xs:
            rx = bx + (s_um / px_size)
            ry = base_y + (o_um / px_size)
            # Rotate into tilted camera sensor frame (+true_angle)
            cx = rx * c - ry * s
            cy = rx * s + ry * c
            pts.append([cx, cy])
        dots = np.array(pts, dtype=float)
        measured_angle = compute_angle_ulab(dots)
        assert abs(measured_angle - true_angle) < 1e-5
        rect = rotate_dots_ulab(dots, measured_angle)
        rect_dots_list.append(rect)

    shifts, _ref = calculate_facet_shifts_ulab(rect_dots_list, pixelsize=px_size)
    assert len(shifts) == 4
    for idx, (s_um, o_um) in enumerate(true_offsets_um):
        # Unified target - virtual_reference sign convention
        assert abs(float(shifts[str(idx)]["Scan_shift_um"]) - s_um) < 1e-2
        assert abs(float(shifts[str(idx)]["Orth_shift_um"]) - o_um) < 1e-2


def test_calibrate_and_pattern_error_endpoints():
    """Verify full on-board 2D facet calibration and Verified Calibration (pattern_error) API."""

    async def run():
        client = TestClient(app)

        # Authorize session
        await client.post(
            "/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="password=hex",
        )

        # 1. Run multi-facet dot calibration
        res_cal = await client.post(
            "/api/calibration/calibrate",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"iterations": 2, "num_dots": 6}),
        )
        assert res_cal.status_code == 200
        cal_data = _json(res_cal)
        assert cal_data["status"] == "success"
        assert "facets" in cal_data
        for f_key in ("0", "1", "2", "3"):
            assert f_key in cal_data["facets"]
            assert "scan_mean_um" in cal_data["facets"][f_key]
            assert "orth_mean_um" in cal_data["facets"][f_key]
            assert "spot_size_um" in cal_data["facets"][f_key]
            assert "period_ms" in cal_data["facets"][f_key]

        # 2. Run Verified Calibration (pattern_error)
        res_ver = await client.post(
            "/api/calibration/pattern_error",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"num_dots": 6, "calibrate_first": True}),
        )
        assert res_ver.status_code == 200
        report = _json(res_ver)
        assert report["status"] == "success"
        assert report["passed"] is True
        assert report["max_error_before_um"] > 30.0
        assert report["max_error_after_um"] <= 30.0
        assert report["max_error_after_um"] < 10.0
        assert report["max_error_after_um"] < report["max_error_before_um"]
        assert len(report["facets"]) == 4

        # 3. Modify Facet 0 custom Scan AND Orth offsets via /api/calibration/update_facets
        #    and re-verify (calibrate_first=False) in true 2D
        custom_facets = [
            {
                "facet": f["facet"],
                "corr_scan_um": f["corr_scan_um"] + (15.0 if f["facet"] == 0 else 0.0),
                "corr_orth_um": f["corr_orth_um"] + (20.0 if f["facet"] == 0 else 0.0),
            }
            for f in report["facets"]
        ]
        res_upd = await client.post(
            "/api/calibration/update_facets",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"facets": custom_facets, "apply_correction": True}),
        )
        assert res_upd.status_code == 200
        assert _json(res_upd)["apply_correction"] is True

        res_custom = await client.post(
            "/api/calibration/pattern_error",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"num_dots": 6, "calibrate_first": False}),
        )
        assert res_custom.status_code == 200
        rep_custom = _json(res_custom)
        assert rep_custom["status"] == "success"
        assert abs(rep_custom["facets"][0]["after_scan_um"]) > 5.0
        assert abs(rep_custom["facets"][0]["after_orth_um"]) > 5.0

    asyncio.run(run())


def test_event_driven_print_loop_and_zero_steps(tmp_path, monkeypatch):
    """Verify BaseLaserhead 24 MHz print_loop, zero-step packets, e1_nocor repeats, and e4_cor non-repeat."""
    import struct
    import zlib

    from control import constants
    from control.laserhead.base import BaseLaserhead
    from hexastorm import ulabext
    from hexastorm.config import Spi

    lh = BaseLaserhead()
    bits_scanline = int(lh.cfg.get_optical_params()["bitsinscanline"])
    scanline_ticks = int(lh.cfg.laser_timing["scanline_ticks"])

    # 1. Verify steps_line=0 produces valid variable-length SPI packets with half_period=0
    zero_step_bits = [1, 0, 1] + [0] * (bits_scanline - 3)
    byte_lst = lh.bit_to_byte_list(zero_step_bits, steps_line=0, direction=1)
    cmd_lst = lh.byte_to_cmd_list(byte_lst)
    assert len(cmd_lst) == 3  # 1 header word + 2 event words (3 transitions -> 2 words)
    assert all(len(pkt) == Spi.command_bytes + Spi.word_bytes for pkt in cmd_lst)
    hdr_payload = int.from_bytes(cmd_lst[0][1:8], "big")
    assert (hdr_payload & 1) == 1
    assert ((hdr_payload >> 1) & 0xFFFF) == 2
    assert (hdr_payload >> 17) == 0

    # 2. Build 2-lane x 4-facet compressed event-driven .pat helper
    def make_line_bits(num_pulses: int) -> list[int]:
        bits = [0] * bits_scanline
        for p in range(num_pulses):
            start = 10 + p * 20
            bits[start : start + 5] = [1] * 5
        return bits

    pulse_counts_per_lane = [
        [0, 1, 2, 3],
        [0, 2, 1, 3],
    ]
    expected_event_words = [0, 1, 2, 3, 0, 2, 1, 3]
    lanes = len(pulse_counts_per_lane)
    facets_lane = len(pulse_counts_per_lane[0])

    def write_pat(filename: str, steps_line: float) -> str:
        compressor = zlib.compressobj()
        pat_bytes = bytearray()
        pat_bytes.extend(
            compressor.compress(struct.pack("<fII", 1.5, facets_lane, lanes))
        )
        for lane_idx, counts in enumerate(pulse_counts_per_lane):
            direction = 1 if lane_idx % 2 == 0 else 0
            for count in counts:
                line_bytes = lh.bit_to_byte_list(
                    make_line_bits(count), steps_line=steps_line, direction=direction
                )
                for pkt in lh.byte_to_cmd_list(line_bytes):
                    pat_bytes.extend(compressor.compress(pkt))
        pat_bytes.extend(compressor.flush())
        (tmp_path / filename).write_bytes(bytes(pat_bytes))
        return filename

    fname_e1 = write_pat("testjob_e1_nocor.pat", steps_line=1.0)
    fname_e4 = write_pat("testjob_e4_cor.pat", steps_line=0.25)

    def parse_scanline_headers(buf: bytes) -> list[tuple[int, int, int]]:
        pkt_len = Spi.command_bytes + Spi.word_bytes
        parsed = []
        idx = 0
        while idx + pkt_len <= len(buf):
            if (
                buf[idx] == Spi.Commands.write
                and buf[idx + pkt_len - 1] == Spi.Instructions.scanline
            ):
                payload = int.from_bytes(buf[idx + 1 : idx + pkt_len - 1], "big")
                direction = payload & 1
                event_words = (payload >> 1) & 0xFFFF
                half_period = payload >> 17
                parsed.append((direction, event_words, half_period))
                idx += (1 + event_words) * pkt_len
            else:
                idx += pkt_len
        return parsed

    monkeypatch.setitem(constants.CONFIG["webserver"], "job_folder", str(tmp_path))
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "home_before_print", False)
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "use_custom_start", False)
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "singlefacet", False)

    async def fast_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    recorded_cmds: list[bytes] = []
    orig_send_command = lh.send_command
    lane_end_x: list[float] = []
    orig_gotopoint = lh.gotopoint

    async def spy_gotopoint(position, **kwargs):
        if not kwargs.get("absolute", True):
            lane_end_x.append(float(lh.mpos[0]))
        return await orig_gotopoint(position, **kwargs)

    async def capture_send_command(command, timeout=0):
        if hasattr(command, "__len__") and len(command) > 0:
            recorded_cmds.append(bytes(command))
        return await orig_send_command(command, timeout=timeout)

    monkeypatch.setattr(lh, "gotopoint", spy_gotopoint)
    monkeypatch.setattr(lh, "send_command", capture_send_command)

    steps_mm_x = float(lh.cfg.motor_cfg["steps_mm"][lh.cfg.motor_cfg["orth2lsrline"]])

    async def run():
        # 3. Verify e1_nocor with GUI exposureperline=1 (8 packets, 1 step/line)
        monkeypatch.setitem(constants.CONFIG["defaultprint"], "exposureperline", 1)
        recorded_cmds.clear()
        lane_end_x.clear()
        await lh.print_loop(fname_e1)

        exp1_headers = [
            hdr for cmd in recorded_cmds for hdr in parse_scanline_headers(cmd)
        ]
        expected_hp_1 = ulabext.calc_step_half_period(1.0, scanline_ticks)
        assert len(exp1_headers) == lanes * facets_lane
        assert [h[1] for h in exp1_headers] == expected_event_words
        assert [h[0] for h in exp1_headers] == [1, 1, 1, 1, 0, 0, 0, 0]
        assert all(h[2] == expected_hp_1 for h in exp1_headers)
        assert abs(lane_end_x[0] - (facets_lane * (1.0 / steps_mm_x))) < 1e-6

        # 4. Verify e1_nocor with GUI exposureperline=4 repeats each line 4x with 24 MHz quarter-step half_period
        exposures = 4
        monkeypatch.setitem(
            constants.CONFIG["defaultprint"], "exposureperline", exposures
        )
        recorded_cmds.clear()
        lane_end_x.clear()
        await lh.print_loop(fname_e1)

        scanline_cmds = [
            cmd for cmd in recorded_cmds if lh._count_scanlines_in_command(cmd) > 0
        ]
        assert len(scanline_cmds) == lanes * facets_lane
        expected_hp_4 = ulabext.calc_step_half_period(1.0 / exposures, scanline_ticks)
        for line_idx, cmd in enumerate(scanline_cmds):
            assert lh._count_scanlines_in_command(cmd) == exposures
            headers = parse_scanline_headers(cmd)
            assert len(headers) == exposures
            expected_dir = 0 if ((line_idx // facets_lane) % 2 == 1) else 1
            for direction, event_words, half_period in headers:
                assert event_words == expected_event_words[line_idx]
                assert direction == expected_dir
                assert half_period == expected_hp_4
        assert abs(lane_end_x[0] - (facets_lane * (1.0 / steps_mm_x))) < 1e-6

        # 5. Verify e4_cor with GUI exposureperline=1 does NOT re-repeat 4x and advances 0.25 step/file line
        monkeypatch.setitem(constants.CONFIG["defaultprint"], "exposureperline", 1)
        recorded_cmds.clear()
        lane_end_x.clear()
        await lh.print_loop(fname_e4)
        e4_headers = [
            hdr for cmd in recorded_cmds for hdr in parse_scanline_headers(cmd)
        ]
        assert len(e4_headers) == lanes * facets_lane
        assert [h[1] for h in e4_headers] == expected_event_words
        assert all(h[2] == expected_hp_4 for h in e4_headers)
        assert abs(lane_end_x[0] - (facets_lane * (0.25 / steps_mm_x))) < 1e-6

    asyncio.run(run())


def test_check_crash_loop_rtc_raises_in_safe_mode(monkeypatch):
    """Verify check_crash_loop_rtc() raises RuntimeError when crash limit is reached."""
    import pytest

    from control import bootlib

    class DummyRTC:
        def memory(self, data=None):
            if data is None:
                return b"3"
            return None

    monkeypatch.setattr(bootlib.machine, "RTC", DummyRTC, raising=False)
    with pytest.raises(RuntimeError, match="SAFE MODE ACTIVATED"):
        bootlib.check_crash_loop_rtc()


def test_print_loop_prep_without_suffix_and_invalid_override(monkeypatch):
    """Verify plain job names work and invalid GUI exposure overrides reset printing."""
    from control import constants
    from control.laserhead.base import BaseLaserhead

    lh = BaseLaserhead()
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "exposureperline", 2)
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "singlefacet", False)

    ok = asyncio.run(lh.print_loop_prep("plain_job.pat"))
    assert ok is True
    assert lh.state["printing"] is True
    assert lh.state["job"]["exposureperline"] == 2

    invalid = asyncio.run(lh.print_loop_prep("job_e1_cor.pat"))
    assert invalid is False
    assert lh.state["printing"] is False  # pyrefly: ignore[unnecessary-comparison]
    assert lh.state["error_message"] is not None


def test_print_loop_aborts_on_sync_failure(tmp_path, monkeypatch):
    """Verify print_loop aborts cleanly without sending scanlines if synchronize fails."""
    import struct
    import zlib

    from control import constants
    from control.laserhead.base import BaseLaserhead

    lh = BaseLaserhead()
    bits_scanline = int(lh.cfg.get_optical_params()["bitsinscanline"])
    compressor = zlib.compressobj()
    pat_bytes = bytearray()
    pat_bytes.extend(compressor.compress(struct.pack("<fII", 1.5, 4, 1)))
    line_bytes = lh.bit_to_byte_list([0] * bits_scanline, steps_line=1, direction=1)
    for pkt in lh.byte_to_cmd_list(line_bytes):
        pat_bytes.extend(compressor.compress(pkt))
    pat_bytes.extend(compressor.flush())

    fname = "sync_fail_e1_nocor.pat"
    (tmp_path / fname).write_bytes(bytes(pat_bytes))

    monkeypatch.setitem(constants.CONFIG["webserver"], "job_folder", str(tmp_path))
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "home_before_print", False)
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "use_custom_start", False)
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "singlefacet", False)
    monkeypatch.setitem(constants.CONFIG["defaultprint"], "exposureperline", 1)

    async def fast_sleep(_delay):
        return None

    async def fail_sync(value=True):
        return False

    sent_scanlines = 0
    orig_send_command = lh.send_command

    async def capture_send_command(command, timeout=0):
        nonlocal sent_scanlines
        if hasattr(command, "__len__") and len(command) > 0:
            sent_scanlines += lh._count_scanlines_in_command(command)
        return await orig_send_command(command, timeout=timeout)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(lh, "synchronize", fail_sync)
    monkeypatch.setattr(lh, "send_command", capture_send_command)

    asyncio.run(lh.print_loop(fname))

    assert sent_scanlines == 0
    assert lh.state["printing"] is False
    assert "synchronization failed" in (lh.state["error_message"] or "")


def test_calibration_esp32_uses_synchronize_kwarg(monkeypatch):
    """Verify calibration routines call enable_comp(..., synchronize=...) on ESP32."""
    import types

    import numpy as np

    from control import calibration, constants

    calls = []

    class StrictLaserhead:
        def __init__(self):
            self.cfg = types.SimpleNamespace(
                laser_timing={"scanline_ticks": 42000, "bitsinscanline": 40, "rpm": 3000, "facets": 4}
            )

        async def synchronize(self, value=True):
            return True

        async def remap(self, facet_id=0):
            return int(facet_id) % 4

        async def enable_comp(
            self,
            laser0=None,
            laser1=None,
            polygon=None,
            synchronize=None,
            singlefacet=None,
        ):
            calls.append((laser0, laser1, polygon, synchronize, singlefacet))

        async def write_events(
            self, events, steps_line=1, direction=0, repetitions=1, facet=None
        ):
            return None

        async def write_line(
            self, bit_lst, steps_line=1, direction=0, repetitions=1, facet=None
        ):
            return None

        async def wait_fifo_empty(self):
            return None

    class DummyAnalysisCtx:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    class DummyCamera:
        def analysis_mode(self, **kwargs):
            return DummyAnalysisCtx()

        def discard_frame(self):
            return None

        def capture_grayscale_array(self):
            return np.zeros((10, 10), dtype=np.uint8)

    monkeypatch.setattr(constants, "ESP32", True)
    monkeypatch.setattr(calibration, "constants", constants)
    monkeypatch.setattr(calibration, "update_config", lambda: None)
    monkeypatch.setitem(
        calibration.CONFIG, "calibration", dict(calibration.CONFIG["calibration"])
    )

    async def fast_sleep(_delay):
        return None

    monkeypatch.setattr(calibration.asyncio, "sleep", fast_sleep)

    result = asyncio.run(
        calibration.run_scan_visibility(
            laserhead=StrictLaserhead(),
            camera=DummyCamera(),
            facet=0,
            step=20,
        )
    )
    assert result["status"] == "success"
    assert any(c[3] is True for c in calls)
    assert all(c[1] is not True for c in calls)


def test_deploy_assets_migrates_existing_config_old(tmp_path, monkeypatch):
    """Verify deploy_assets() migrates pre-existing config_old.json when config.json is absent."""
    import sys
    import types

    from control import constants

    old_cfg = tmp_path / "config_old.json"
    new_cfg = tmp_path / "config.json"
    old_cfg.write_text(
        json.dumps({"serial": "CUSTOM-999", "wifi_login": {"ssid": "LabWiFi"}})
    )

    class StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path, target=None):
            if fullname in ("control.build_info", "control.frozen_root"):
                return importlib.util.spec_from_loader(fullname, self)
            return None

        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            if module.__name__ == "control.build_info":
                setattr(module, "BUILD_ID", "build_test_123")
            elif module.__name__ == "control.frozen_root":
                new_cfg.write_text(
                    json.dumps(
                        {
                            "serial": "DEFAULT-000",
                            "wifi_login": {"ssid": "", "password": "secret"},
                            "new_key": 42,
                        }
                    )
                )

    finder = StubFinder()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "control.build_info", raising=False)
    monkeypatch.delitem(sys.modules, "control.frozen_root", raising=False)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    monkeypatch.setattr(constants, "ESP32", True)
    monkeypatch.setattr(constants, "CONFIG_FILE", str(new_cfg))
    monkeypatch.setattr(constants, "CONFIG_OLD_FILE", str(old_cfg))

    constants.deploy_assets(overwrite=True)

    merged = json.loads(new_cfg.read_text())
    assert merged["serial"] == "CUSTOM-999"
    assert merged["wifi_login"]["ssid"] == "LabWiFi"
    assert merged["wifi_login"]["password"] == "secret"
    assert merged["new_key"] == 42
    assert not old_cfg.exists()
