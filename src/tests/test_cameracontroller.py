import asyncio
import json

from control.camera import camera
from control.webapp import app
from microdot.test_client import TestClient


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

        # Unauthorized access check (redirects to login)
        res_unauth = await client.get("/api/camera/snapshot")
        assert res_unauth.status_code == 302

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
        assert res.body.startswith(b"\xff\xd8")
        assert res.body.endswith(b"\xff\xd9")

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
        data = res.json
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
        data = res.json
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
        data = res.json
        assert data["status"] == "success"
        assert "visible_pixels" in data
        assert data["visible_pixels"] == [180, 520]
        assert data["percentage"] > 0

        # 2. Toggle show_bounds ON
        res_bounds_on = await client.post(
            "/api/calibration/show_bounds",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"enabled": True, "facet": 0}),
        )
        assert res_bounds_on.status_code == 200
        assert res_bounds_on.json["bounds_active"] is True
        assert res_bounds_on.json["visible_pixels"] == [180, 520]

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
        assert res_bounds_off.json["bounds_active"] is False

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

    # Zero-mean facet offsets in um so average across 4 facets is 0
    true_offsets_um = [
        (20.0, -10.0),
        (-35.0, 25.0),
        (10.0, -20.0),
        (5.0, 5.0),
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

    shifts = calculate_facet_shifts_ulab(rect_dots_list, pixelsize=px_size)
    assert shifts.shape == (4, 2)
    for idx, (s_um, o_um) in enumerate(true_offsets_um):
        # Required correction shift is -offset (since mean offset is 0)
        assert abs(float(shifts[idx, 0]) - (-s_um)) < 1e-4
        assert abs(float(shifts[idx, 1]) - (-o_um)) < 1e-4


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
        cal_data = res_cal.json
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
        report = res_ver.json
        assert report["status"] == "success"
        assert report["passed"] is True
        assert report["max_error_before_um"] > 30.0
        assert report["max_error_after_um"] <= 30.0
        assert report["max_error_after_um"] < 2.0
        assert report["max_error_after_um"] < report["max_error_before_um"]
        assert len(report["facets"]) == 4

        # 3. Modify Facet 0 custom offset via /api/calibration/update_facets and re-verify (calibrate_first=False)
        custom_facets = [
            {
                "facet": f["facet"],
                "corr_scan_um": f["corr_scan_um"] + (15.0 if f["facet"] == 0 else 0.0),
                "corr_orth_um": f["corr_orth_um"],
            }
            for f in report["facets"]
        ]
        res_upd = await client.post(
            "/api/calibration/update_facets",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"facets": custom_facets, "apply_correction": True}),
        )
        assert res_upd.status_code == 200
        assert res_upd.json["apply_correction"] is True

        res_custom = await client.post(
            "/api/calibration/pattern_error",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"num_dots": 6, "calibrate_first": False}),
        )
        assert res_custom.status_code == 200
        rep_custom = res_custom.json
        assert rep_custom["status"] == "success"
        # Since Facet 0 was shifted by +15 um relative to the ideal correction, its residual error increases
        assert abs(rep_custom["facets"][0]["after_scan_um"]) > 5.0

    asyncio.run(run())


def test_event_driven_print_loop_and_zero_steps(tmp_path, monkeypatch):
    """Verify BaseLaserhead handles steps_line=0 and variable-length event-driven .pat files."""
    import struct
    import zlib

    from control import constants
    from control.laserhead.base import BaseLaserhead
    from hexastorm.config import Spi

    lh = BaseLaserhead()
    bits_scanline = int(lh.cfg.laser_timing["scanline_length"])

    # 1. Verify steps_line=0 produces valid variable-length SPI packets
    zero_step_bits = [1, 0, 1] + [0] * (bits_scanline - 3)
    byte_lst = lh.bit_to_byte_list(zero_step_bits, steps_line=0, direction=1)
    cmd_lst = lh.byte_to_cmd_list(byte_lst)
    assert len(cmd_lst) == 3  # 1 header word + 2 event words (3 transitions -> 2 words)
    assert all(len(pkt) == Spi.command_bytes + Spi.word_bytes for pkt in cmd_lst)
    hdr_payload = int.from_bytes(cmd_lst[0][1:8], "big")
    assert (hdr_payload & 1) == 1
    assert ((hdr_payload >> 1) & 0xFFFF) == 2
    assert (hdr_payload >> 17) == bits_scanline

    # 2. Build a 2-lane x 4-facet compressed event-driven .pat file with varying event counts
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
    expected_event_words = [
        0,
        1,
        2,
        3,
        0,
        2,
        1,
        3,
    ]  # 2*k events -> k 64-bit words
    lanes = len(pulse_counts_per_lane)
    facets_lane = len(pulse_counts_per_lane[0])

    compressor = zlib.compressobj()
    pat_bytes = bytearray()
    pat_bytes.extend(compressor.compress(struct.pack("<fII", 1.5, facets_lane, lanes)))
    for lane_idx, counts in enumerate(pulse_counts_per_lane):
        direction = 1 if lane_idx % 2 == 0 else 0
        for count in counts:
            line_bytes = lh.bit_to_byte_list(
                make_line_bits(count), steps_line=1, direction=direction
            )
            for pkt in lh.byte_to_cmd_list(line_bytes):
                pat_bytes.extend(compressor.compress(pkt))
    pat_bytes.extend(compressor.flush())

    fname = "testjob_e1_nocor.pat"
    (tmp_path / fname).write_bytes(bytes(pat_bytes))

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

    async def capture_send_command(command, timeout=0):
        if hasattr(command, "__len__") and len(command) > 0:
            recorded_cmds.append(bytes(command))
        return await orig_send_command(command, timeout=timeout)

    monkeypatch.setattr(lh, "send_command", capture_send_command)

    async def run():
        # 3. Verify exposures == 1
        monkeypatch.setitem(constants.CONFIG["defaultprint"], "exposureperline", 1)
        recorded_cmds.clear()
        await lh.print_loop(fname)

        exp1_headers = [
            hdr for cmd in recorded_cmds for hdr in parse_scanline_headers(cmd)
        ]
        assert len(exp1_headers) == lanes * facets_lane
        assert [h[1] for h in exp1_headers] == expected_event_words
        assert [h[0] for h in exp1_headers] == [1, 1, 1, 1, 0, 0, 0, 0]

        # 4. Verify exposures == 4 preserves event_words and updates half_period/direction
        exposures = 4
        monkeypatch.setitem(
            constants.CONFIG["defaultprint"], "exposureperline", exposures
        )
        recorded_cmds.clear()
        await lh.print_loop(fname)

        scanline_cmds = [
            cmd for cmd in recorded_cmds if lh._count_scanlines_in_command(cmd) > 0
        ]
        assert len(scanline_cmds) == lanes * facets_lane
        expected_hp = int((bits_scanline - 1) // ((1 / exposures) * 2))
        for line_idx, cmd in enumerate(scanline_cmds):
            assert lh._count_scanlines_in_command(cmd) == exposures
            headers = parse_scanline_headers(cmd)
            assert len(headers) == exposures
            expected_dir = 0 if ((line_idx // facets_lane) % 2 == 1) else 1
            for direction, event_words, half_period in headers:
                assert event_words == expected_event_words[line_idx]
                assert direction == expected_dir
                assert half_period == expected_hp

    asyncio.run(run())
