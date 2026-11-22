"""Hexastorm optical calibration controller running on ESP32-S3 and CPython mock.

All shared DSP, 2D centroiding, global rotation rectification, spatial-median shift
computation, 24 MHz dot-grid slicing, and virtual 2D stacking live in
``hexastorm.calibration.core``. This module orchestrates the OV2640 camera capture,
FPGA SPI locking, configuration persistence, and Web UI endpoints.
"""

import asyncio
import gc
import logging
import math

from camera import Camera, FrameSize, PixelFormat
from hexastorm import ulabext
from hexastorm.calibration.core import (
    PrismCurveLite,
    VirtualStackerUlab,
    analyze_facets_ulab,
    calculate_facet_shifts_ulab,
    compute_angle_ulab,
    get_dots_ulab,
    make_dot_centers_mm,
    make_tick_dot_events,
    rotate_dots_ulab,
    slice_dot_grid_events,
    subtract_dark_frame_u8,
)

from . import constants
from .constants import (
    CALIBRATION_TOLERANCE_UM,
    CONFIG,
    SVGA_PIXEL_SIZE_UM,
    update_config,
)

calibration_ulab = analyze_facets_ulab

__all__ = [
    "PrismCurveLite",
    "VirtualStackerUlab",
    "analyze_facets_ulab",
    "calculate_facet_shifts_ulab",
    "calibration_ulab",
    "capture_jpeg_with_preview",
    "compute_angle_ulab",
    "get_dots_ulab",
    "get_preview_facet",
    "is_bounds_active",
    "make_dot_centers_mm",
    "make_tick_dot_events",
    "rotate_dots_ulab",
    "run_facet_calibration",
    "run_pattern_error",
    "run_scan_visibility",
    "set_bounds_preview",
    "slice_dot_grid_events",
    "subtract_dark_frame_u8",
    "update_custom_facets",
]

logger = logging.getLogger(__name__)

# Global state for "Preview Dots" / "Show Bounds" preview
_bounds_active = False
_preview_facet = 0  # Default to Facet 0 (F1), or None for all 4 facets
_bounds_feeder_task = None
_fpga_calib_lock = asyncio.Lock()

# Initialize mock camera calibration state from persisted CONFIG
Camera.mock_calibrated_facets = dict(CONFIG["calibration"]["facets"])
Camera.mock_apply_correction = bool(CONFIG["calibration"]["apply_correction"])


def _get_optical_context(laserhead) -> tuple[dict, PrismCurveLite, int]:
    """Extract optical params, PrismCurveLite, and software scan length from laserhead."""
    if hasattr(laserhead.cfg, "get_optical_params"):
        params = laserhead.cfg.get_optical_params(correction=False, exposures=1)
        scan_len = int(params["bitsinscanline"])
    else:
        laz = laserhead.cfg.laser_timing
        scan_len = int(laz.get("bitsinscanline", laz["scanline_ticks"]))
        params = dict(laz)
        params.setdefault("start_frac", 0.35)
        params.setdefault("end_frac", 0.70)
        params.setdefault("inradius", 15.0)
        params.setdefault("n", 1.49)
        params.setdefault("tiltangle", math.pi * 0.5)
        params.setdefault("f0_scan", 0.0)
    curve = PrismCurveLite(params, invert_hw_scan_dir=True)
    return params, curve, scan_len


def _bounds_to_u_range(
    bounds: list[int] | None, scan_len: int, curve: PrismCurveLite
) -> tuple[float, float]:
    """Map [lower_px, upper_px] visibility indices into physical [u_min_mm, u_max_mm]."""
    if not bounds or len(bounds) != 2:
        return curve.lanewidth * 0.25, curve.lanewidth * 0.75
    lower, upper = bounds[0], bounds[1]
    t0 = (lower * curve.total_ticks) / max(1, scan_len)
    t1 = ((upper + 1) * curve.total_ticks) / max(1, scan_len)
    u0 = curve.tick_to_u(t0)
    u1 = curve.tick_to_u(t1)
    return min(u0, u1), max(u0, u1)


def _get_orth_steps_mm(laserhead) -> float:
    """Return physical motor steps/mm on the orthogonal stage axis."""
    mtr = getattr(laserhead.cfg, "motor_cfg", None)
    if mtr and "orth2lsrline" in mtr and "steps_mm" in mtr:
        return float(mtr["steps_mm"][mtr["orth2lsrline"]])
    return float(CONFIG["motors"]["x"]["steps_mm"])


def _calc_exposure_reps(laserhead) -> int:
    rpm = float(laserhead.cfg.laser_timing["rpm"])
    rev_time_ms = 60000.0 / max(600.0, rpm)
    exposure_ms = float(CONFIG["calibration"]["camera_exposure_ms"])
    return max(10, min(32, int((exposure_ms * 2.0) / rev_time_ms) + 6))


async def _send_events(laserhead, events, steps_line=0, repetitions=1, facet=None):
    """Send 24 MHz events using write_events if available, else write_line fallback."""
    if hasattr(laserhead, "write_events"):
        await laserhead.write_events(
            events, steps_line=steps_line, repetitions=repetitions, facet=facet
        )
    else:
        _params, curve, scan_len = _get_optical_context(laserhead)
        bits = (
            ulabext.events_to_bits(events, scan_len, curve.total_ticks)
            if events
            else []
        )
        await laserhead.write_line(
            bits, steps_line=steps_line, repetitions=repetitions, facet=facet
        )


def _median_list(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 == 1 else 0.5 * (float(s[mid - 1]) + float(s[mid]))


async def project_and_capture_facet(
    laserhead,
    camera,
    events: list[tuple[int, int]] | None = None,
    line: list[int] | None = None,
    facet: int = 0,
):
    """Project 24 MHz events (or 1D binary list) on one facet and capture a frame."""
    if events is None and line is not None:
        total_ticks = int(laserhead.cfg.laser_timing["scanline_ticks"])
        events = ulabext.bits_to_events(line, total_ticks)
    events = events or []

    async with _fpga_calib_lock:
        true_facet = await laserhead.remap(facet)

        Camera.mock_laser_events = events
        Camera.mock_laser_pattern = None
        Camera.mock_active_facet = facet
        Camera.mock_v_stage_mm = 0.0

        if constants.ESP32:
            reps = _calc_exposure_reps(laserhead)
            await _send_events(
                laserhead, events, steps_line=0, repetitions=reps, facet=true_facet
            )
            camera.discard_frame()
            frame = camera.capture_grayscale_array()
            await _send_events(laserhead, None)
            await laserhead.wait_fifo_empty()
        else:
            await _send_events(
                laserhead, events, steps_line=0, repetitions=3, facet=true_facet
            )
            frame = camera.capture_grayscale_array()

        return frame


async def run_scan_visibility(
    laserhead,
    camera,
    facet: int = 0,
    step: int = 20,
    progress_callback=None,
):
    """Scan full scanline to determine which coordinates fall within sensor FOV."""
    _params, curve, scan_len = _get_optical_context(laserhead)
    pixels_to_scan = list(range(0, scan_len, step))
    if (scan_len - 1) not in pixels_to_scan:
        pixels_to_scan.append(scan_len - 1)

    visible_indices = []
    total_steps = len(pixels_to_scan)

    logger.info(
        f"Starting Scan Visibility: len={scan_len}, step={step}, "
        f"facet={facet}, checks={total_steps}"
    )

    try:
        if constants.ESP32:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=False, polygon=False, synchronize=True
            )

        with camera.analysis_mode(
            frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
        ):
            # 1. Dark Frame Capture (laser OFF, do not force hardware override pin ON)
            Camera.mock_laser_events = None
            Camera.mock_laser_pattern = None
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=False)
            await asyncio.sleep(0.05)
            if constants.ESP32:
                camera.discard_frame()
            dark_frame = camera.capture_grayscale_array()

            # 2. Iterate and detect signal using 24 MHz tick pulses
            for idx_num, px in enumerate(pixels_to_scan):
                t_start = (px * curve.total_ticks) // max(1, scan_len)
                t_end = min(
                    curve.total_ticks, ((px + 1) * curve.total_ticks) // max(1, scan_len)
                )
                events = [(t_start, 255)]
                if t_end < curve.total_ticks:
                    events.append((max(t_start + 1, t_end), 0))

                frame = await project_and_capture_facet(
                    laserhead, camera, events=events, facet=facet
                )
                dots, metrics = get_dots_ulab(frame, dark_frame=dark_frame)

                if dots.shape[0] > 0 or metrics["peak_intensity"] >= 40:
                    visible_indices.append(px)

                del frame
                if idx_num % 8 == 0:
                    gc.collect()

                if progress_callback:
                    pct = int(((idx_num + 1) / total_steps) * 100)
                    await progress_callback(pct, px)

    finally:
        Camera.mock_laser_events = None
        Camera.mock_laser_pattern = None

    if visible_indices:
        bounds = [min(visible_indices), max(visible_indices)]
        visible_count = bounds[1] - bounds[0] + 1
        percentage = round((visible_count / scan_len) * 100, 1)
    else:
        bounds = None
        visible_count = 0
        percentage = 0.0

    if "calibration" not in CONFIG:
        CONFIG["calibration"] = {}
    CONFIG["calibration"]["visible_pixels"] = bounds
    update_config()

    logger.info(
        f"Scan Visibility complete: visible_pixels={bounds} "
        f"({visible_count}/{scan_len} px, {percentage}%)"
    )

    return {
        "status": "success",
        "facet": facet,
        "step": step,
        "scanline_length": scan_len,
        "visible_pixels": bounds,
        "count": visible_count,
        "percentage": percentage,
    }


def _read_period_means(laserhead, shift: int, num_facets: int) -> list[float]:
    raw_means = getattr(laserhead, "cur_facet_means", None)
    if raw_means and len(raw_means) == num_facets:
        return [float(m) for m in (raw_means[shift:] + raw_means[:shift])]
    return [5.0] * num_facets


async def run_facet_calibration(
    laserhead,
    camera,
    iterations: int = 2,
    num_dots: int = 6,
    progress_callback=None,
):
    """Run multi-facet 1D dot calibration and persist facet offsets to config."""
    _params, curve, scan_len = _get_optical_context(laserhead)
    num_facets = int(laserhead.cfg.laser_timing["facets"])
    bounds = CONFIG["calibration"]["visible_pixels"]
    u_min, u_max = _bounds_to_u_range(bounds, scan_len, curve)
    centers = make_dot_centers_mm(u_min, u_max, num_dots=num_dots, margin_mm=0.25)
    base_events = make_tick_dot_events(centers, dot_width_mm=0.02, curve=curve)

    all_iter_results = []
    total_captures = max(1, iterations * num_facets)
    done_captures = 0

    try:
        if constants.ESP32:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=False, polygon=False, synchronize=True
            )

        with camera.analysis_mode(
            frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
        ):
            Camera.mock_laser_events = None
            Camera.mock_laser_pattern = None
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=False)
            await asyncio.sleep(0.05)
            if constants.ESP32:
                camera.discard_frame()
            dark_frame = camera.capture_grayscale_array()

            for _it in range(iterations):
                facet_frames = []
                for facet in range(num_facets):
                    frame = await project_and_capture_facet(
                        laserhead, camera, events=base_events, facet=facet
                    )
                    facet_frames.append(frame)
                    done_captures += 1
                    if progress_callback:
                        pct = int((done_captures / total_captures) * 100)
                        await progress_callback(pct, facet)

                res = analyze_facets_ulab(
                    facet_frames,
                    dark_frame=dark_frame,
                    pixelsize=SVGA_PIXEL_SIZE_UM,
                    compute_rotation=True,
                )
                all_iter_results.append(res)
                del facet_frames
                gc.collect()

    finally:
        Camera.mock_laser_events = None
        Camera.mock_laser_pattern = None

    shift = await laserhead.remap(0)
    period_means = _read_period_means(laserhead, shift, num_facets)

    facets_cfg = {}
    for facet in range(num_facets):
        f_key = str(facet)
        scan_vals = [float(r[f_key]["Scan_shift_um"]) for r in all_iter_results]
        orth_vals = [float(r[f_key]["Orth_shift_um"]) for r in all_iter_results]
        spot_vals = [float(r[f_key]["mean_spot_size_um"]) for r in all_iter_results]
        ecc_vals = [float(r[f_key]["mean_eccentricity"]) for r in all_iter_results]
        rot_vals = [float(r[f_key]["global_angle_deg"]) for r in all_iter_results]

        scan_mean = sum(scan_vals) / len(scan_vals)
        orth_mean = sum(orth_vals) / len(orth_vals)
        scan_std = (
            math.sqrt(sum((v - scan_mean) ** 2 for v in scan_vals) / len(scan_vals))
            if len(scan_vals) > 1
            else 0.0
        )
        orth_std = (
            math.sqrt(sum((v - orth_mean) ** 2 for v in orth_vals) / len(orth_vals))
            if len(orth_vals) > 1
            else 0.0
        )
        facets_cfg[f_key] = {
            "scan": round(scan_mean / 1000.0, 6),
            "orth": round(orth_mean / 1000.0, 6),
            "scan_mean_um": round(scan_mean, 2),
            "scan_std_um": round(scan_std, 2),
            "orth_mean_um": round(orth_mean, 2),
            "orth_std_um": round(orth_std, 2),
            "spot_size_um": round(sum(spot_vals) / len(spot_vals), 2),
            "eccentricity": round(sum(ecc_vals) / len(ecc_vals), 3),
            "rotation_deg": round(sum(rot_vals) / len(rot_vals), 4),
            "period_ms": round(float(period_means[facet]), 4),
        }

    if "calibration" not in CONFIG:
        CONFIG["calibration"] = {}
    CONFIG["calibration"]["facets"] = facets_cfg
    CONFIG["calibration"]["apply_correction"] = True
    Camera.mock_calibrated_facets = facets_cfg
    Camera.mock_apply_correction = True
    update_config()

    return {
        "status": "success",
        "iterations": iterations,
        "facets": facets_cfg,
    }


async def run_pattern_error(
    laserhead,
    camera,
    num_dots: int = 6,
    calibrate_first: bool = True,
    progress_callback=None,
):
    """Execute closed-loop 2D Verified Calibration using analytical slicing + VirtualStackerUlab.

    1. Captures dark frame and obtains camera tilt angle (either via 1D baseline capture when
       ``calibrate_first=True`` / missing config, or from stored calibration).
    2. Slices & projects an Uncorrected 2D dot grid per facet and stacks frames with
       ``VirtualStackerUlab`` to measure true 2D Scan/Orth errors Before.
    3. Slices & projects the Corrected 2D dot grid at 24 MHz tick & sub-step resolution and
       stacks frames with ``VirtualStackerUlab`` to measure true 2D errors After.
    4. Verifies max_error_after <= 30 um and strict improvement over Uncorrected.
    """
    params, curve, scan_len = _get_optical_context(laserhead)
    num_facets = int(laserhead.cfg.laser_timing["facets"])
    steps_mm = _get_orth_steps_mm(laserhead)
    exposures_2d = 4
    dot_diameter_mm = 0.08
    bounds = CONFIG["calibration"]["visible_pixels"]
    u_min, u_max = _bounds_to_u_range(bounds, scan_len, curve)

    try:
        if constants.ESP32:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=False, polygon=False, synchronize=True
            )

        with camera.analysis_mode(
            frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
        ):
            # 1. Dark Frame
            Camera.mock_laser_events = None
            Camera.mock_laser_pattern = None
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=False)
            await asyncio.sleep(0.05)
            if constants.ESP32:
                camera.discard_frame()
            dark_frame = camera.capture_grayscale_array()

            shift = await laserhead.remap(0)
            period_means = _read_period_means(laserhead, shift, num_facets)
            facets_cfg = dict(CONFIG["calibration"].get("facets") or {})

            # 2. Obtain 1D baseline calibration (when requested or uncalibrated)
            if calibrate_first or len(facets_cfg) < num_facets:
                centers_1d = make_dot_centers_mm(
                    u_min, u_max, num_dots=num_dots, margin_mm=0.25
                )
                events_1d = make_tick_dot_events(
                    centers_1d, dot_width_mm=0.02, curve=curve
                )
                frames_1d = []
                for facet in range(num_facets):
                    frames_1d.append(
                        await project_and_capture_facet(
                            laserhead, camera, events=events_1d, facet=facet
                        )
                    )
                res_1d = analyze_facets_ulab(
                    frames_1d,
                    dark_frame=dark_frame,
                    pixelsize=SVGA_PIXEL_SIZE_UM,
                    compute_rotation=True,
                )
                global_angle = float(res_1d["0"]["global_angle_deg"])
                facets_cfg = {}
                for facet in range(num_facets):
                    f_key = str(facet)
                    s_um = float(res_1d[f_key]["Scan_shift_um"])
                    o_um = float(res_1d[f_key]["Orth_shift_um"])
                    facets_cfg[f_key] = {
                        "scan": round(s_um / 1000.0, 6),
                        "orth": round(o_um / 1000.0, 6),
                        "scan_mean_um": round(s_um, 2),
                        "scan_std_um": 0.0,
                        "orth_mean_um": round(o_um, 2),
                        "orth_std_um": 0.0,
                        "spot_size_um": res_1d[f_key]["mean_spot_size_um"],
                        "eccentricity": res_1d[f_key]["mean_eccentricity"],
                        "rotation_deg": global_angle,
                        "period_ms": round(float(period_means[facet]), 4),
                    }
                del frames_1d
                gc.collect()
            else:
                rot_angles = [
                    float(facets_cfg[str(f)].get("rotation_deg", 0.0))
                    for f in range(num_facets)
                    if str(f) in facets_cfg
                ]
                global_angle = _median_list(rot_angles) if rot_angles else 0.0

            async def _run_2d_pass(
                offsets_mm: dict[int, tuple[float, float]], progress_base: int
            ) -> dict:
                plan = slice_dot_grid_events(
                    u_min_mm=u_min,
                    u_max_mm=u_max,
                    params=params,
                    num_dots_x=num_dots,
                    num_dots_y=1,
                    dot_diameter_mm=dot_diameter_mm,
                    exposures=exposures_2d,
                    steps_mm=steps_mm,
                    facet_offsets_mm=offsets_mm,
                )
                total_rows = plan["total_rows"]
                active_rows = plan["rows"]
                stackers = [
                    VirtualStackerUlab(
                        expected_total_steps=total_rows,
                        exposures=exposures_2d,
                        px_size_um=SVGA_PIXEL_SIZE_UM,
                        steps_mm=steps_mm,
                        rotation_angle_deg=global_angle,
                    )
                    for _ in range(num_facets)
                ]
                n_active = max(1, len(active_rows))
                for idx, (row_idx, facet, events) in enumerate(active_rows):
                    frame = await project_and_capture_facet(
                        laserhead, camera, events=events, facet=facet
                    )
                    stackers[facet].add_exposure(
                        frame, num_steps=row_idx, dark_frame=dark_frame
                    )
                    del frame
                    if idx % 8 == 0:
                        gc.collect()
                    if progress_callback:
                        pct = progress_base + int(((idx + 1) / n_active) * 50)
                        await progress_callback(pct, facet)

                stacked = [s.get_result() for s in stackers]
                res = analyze_facets_ulab(
                    stacked,
                    dark_frame=None,
                    pixelsize=SVGA_PIXEL_SIZE_UM,
                    compute_rotation=False,
                    rotation_angle_deg=global_angle,
                    max_diameter_um=320.0,
                )
                del stacked, stackers
                gc.collect()
                return res

            # 3. Uncorrected 2D pass (Before)
            zero_offsets = {f: (0.0, 0.0) for f in range(num_facets)}
            before_res = await _run_2d_pass(zero_offsets, progress_base=0)

            # 4. Corrected 2D pass (After)
            corr_offsets = {
                f: (
                    float(facets_cfg[str(f)]["scan"]),
                    float(facets_cfg[str(f)]["orth"]),
                )
                for f in range(num_facets)
            }
            after_res = await _run_2d_pass(corr_offsets, progress_base=50)

    finally:
        Camera.mock_laser_events = None
        Camera.mock_laser_pattern = None

    per_facet_report = []
    before_abs_vals = []
    after_abs_vals = []

    for facet in range(num_facets):
        f_key = str(facet)
        b_scan = float(before_res[f_key]["Scan_shift_um"])
        b_orth = float(before_res[f_key]["Orth_shift_um"])
        b_tot = float(before_res[f_key]["Total_error_um"])

        a_scan = float(after_res[f_key]["Scan_shift_um"])
        a_orth = float(after_res[f_key]["Orth_shift_um"])
        a_tot = float(after_res[f_key]["Total_error_um"])

        before_abs_vals.extend([abs(b_scan), abs(b_orth)])
        after_abs_vals.extend([abs(a_scan), abs(a_orth)])

        per_facet_report.append(
            {
                "facet": facet,
                "before_scan_um": round(b_scan, 2),
                "before_orth_um": round(b_orth, 2),
                "before_total_um": round(b_tot, 2),
                "corr_scan_um": round(float(facets_cfg[f_key]["scan_mean_um"]), 2),
                "corr_orth_um": round(float(facets_cfg[f_key]["orth_mean_um"]), 2),
                "after_scan_um": round(a_scan, 2),
                "after_orth_um": round(a_orth, 2),
                "after_total_um": round(a_tot, 2),
                "spot_size_um": after_res[f_key]["mean_spot_size_um"],
                "eccentricity": after_res[f_key]["mean_eccentricity"],
                "rotation_deg": after_res[f_key]["rotation_angle_deg"],
                "period_ms": facets_cfg[f_key]["period_ms"],
            }
        )

    max_err_before = round(max(before_abs_vals) if before_abs_vals else 0.0, 2)
    max_err_after = round(max(after_abs_vals) if after_abs_vals else 0.0, 2)
    mae_before = round(
        sum(before_abs_vals) / len(before_abs_vals) if before_abs_vals else 0.0, 2
    )
    mae_after = round(
        sum(after_abs_vals) / len(after_abs_vals) if after_abs_vals else 0.0, 2
    )

    within_tolerance = max_err_after <= CALIBRATION_TOLERANCE_UM
    improved = (
        (max_err_after < max_err_before)
        if max_err_before > CALIBRATION_TOLERANCE_UM
        else True
    )
    passed = within_tolerance and improved

    report = {
        "status": "success",
        "passed": passed,
        "tolerance_um": CALIBRATION_TOLERANCE_UM,
        "max_error_before_um": max_err_before,
        "max_error_after_um": max_err_after,
        "mae_before_um": mae_before,
        "mae_after_um": mae_after,
        "facets": per_facet_report,
    }

    if "calibration" not in CONFIG:
        CONFIG["calibration"] = {}
    CONFIG["calibration"]["facets"] = facets_cfg
    CONFIG["calibration"]["last_report"] = report
    CONFIG["calibration"]["apply_correction"] = True
    Camera.mock_calibrated_facets = facets_cfg
    Camera.mock_apply_correction = True
    update_config()

    logger.info(
        f"Verified Calibration complete: passed={passed}, "
        f"max_before={max_err_before} um -> max_after={max_err_after} um"
    )
    return report


def update_custom_facets(
    facets_override: dict | list | None = None,
    apply_correction: bool | None = None,
) -> dict:
    """Update per-facet offsets and/or apply_correction flag."""
    facets_cfg = dict(CONFIG["calibration"]["facets"])

    if isinstance(facets_override, list):
        for item in facets_override:
            f_key = str(item["facet"])
            s_um = round(float(item["corr_scan_um"]), 2)
            o_um = round(float(item["corr_orth_um"]), 2)
            cur = dict(facets_cfg[f_key]) if f_key in facets_cfg else {"period_ms": 5.0}
            cur.update(
                {
                    "scan": round(s_um / 1000.0, 6),
                    "orth": round(o_um / 1000.0, 6),
                    "scan_mean_um": s_um,
                    "orth_mean_um": o_um,
                    "period_ms": float(cur["period_ms"]),
                }
            )
            facets_cfg[f_key] = cur
    elif isinstance(facets_override, dict):
        for f_key_raw, item in facets_override.items():
            f_key = str(f_key_raw)
            s_um = round(float(item["corr_scan_um"]), 2)
            o_um = round(float(item["corr_orth_um"]), 2)
            cur = dict(facets_cfg[f_key]) if f_key in facets_cfg else {"period_ms": 5.0}
            cur.update(
                {
                    "scan": round(s_um / 1000.0, 6),
                    "orth": round(o_um / 1000.0, 6),
                    "scan_mean_um": s_um,
                    "orth_mean_um": o_um,
                    "period_ms": float(cur["period_ms"]),
                }
            )
            facets_cfg[f_key] = cur

    CONFIG["calibration"]["facets"] = facets_cfg
    if apply_correction is not None:
        CONFIG["calibration"]["apply_correction"] = apply_correction

    last_rep = CONFIG["calibration"]["last_report"]
    if isinstance(last_rep, dict):
        for entry in last_rep["facets"]:
            f_key = str(entry["facet"])
            if f_key in facets_cfg:
                entry["corr_scan_um"] = facets_cfg[f_key]["scan_mean_um"]
                entry["corr_orth_um"] = facets_cfg[f_key]["orth_mean_um"]

    Camera.mock_calibrated_facets = facets_cfg
    Camera.mock_apply_correction = bool(CONFIG["calibration"]["apply_correction"])
    update_config()
    return {
        "status": "success",
        "apply_correction": Camera.mock_apply_correction,
        "facets": facets_cfg,
        "last_report": last_rep,
    }


async def _bounds_hardware_feeder(laserhead, events: list[tuple[int, int]], facet: int | None = None):
    """ESP32 background task feeding FPGA FIFO while Preview Dots is active."""
    try:
        async with _fpga_calib_lock:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=False, polygon=False, synchronize=True
            )
            true_facet = (await laserhead.remap(facet)) if facet is not None else None
        while _bounds_active:
            async with _fpga_calib_lock:
                await _send_events(
                    laserhead, events, steps_line=0, repetitions=12, facet=true_facet
                )
            await asyncio.sleep(0.15)
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Bounds feeder error: {exc}")
    finally:
        try:
            async with _fpga_calib_lock:
                await _send_events(laserhead, None)
                await laserhead.wait_fifo_empty()
        except Exception:  # noqa: BLE001, S110
            pass


def _build_preview_events(laserhead, num_dots: int = 6) -> list[tuple[int, int]]:
    _params, curve, scan_len = _get_optical_context(laserhead)
    bounds = CONFIG["calibration"]["visible_pixels"]
    u_min, u_max = _bounds_to_u_range(bounds, scan_len, curve)
    centers = make_dot_centers_mm(u_min, u_max, num_dots=num_dots, margin_mm=0.25)
    scan_offset_mm = 0.0
    facets_cfg = CONFIG["calibration"].get("facets") or {}
    apply_corr = bool(CONFIG["calibration"].get("apply_correction"))
    if _preview_facet is not None and apply_corr and str(_preview_facet) in facets_cfg:
        scan_offset_mm = float(facets_cfg[str(_preview_facet)]["scan"])
    return make_tick_dot_events(
        centers, dot_width_mm=0.02, curve=curve, scan_offset_mm=scan_offset_mm
    )


def set_bounds_preview(
    laserhead,
    enabled: bool | None = None,
    facet: int | str | None = "keep",
    num_dots: int = 6,
) -> bool:
    """Toggle or set 'Preview Dots' projecting dots on 1 or all facets."""
    global _bounds_active, _preview_facet, _bounds_feeder_task

    if enabled is None:
        _bounds_active = not _bounds_active
    else:
        _bounds_active = enabled

    if facet != "keep":
        if facet is None or facet == "all" or facet == -1 or facet == "-1":
            _preview_facet = None
        else:
            _preview_facet = int(facet)

    bounds = CONFIG["calibration"]["visible_pixels"]

    if _bounds_active:
        events = _build_preview_events(laserhead, num_dots=num_dots)
        Camera.mock_laser_events = events
        Camera.mock_laser_pattern = None
        Camera.mock_preview_facet = _preview_facet
        facet_label = (
            f"facet {_preview_facet}" if _preview_facet is not None else "all facets"
        )
        logger.info(
            f"Preview Dots ENABLED ({num_dots} dots on {facet_label}, bounds={bounds})"
        )

        if constants.ESP32:
            if _bounds_feeder_task is not None:
                try:
                    _bounds_feeder_task.cancel()
                except Exception:  # noqa: BLE001, S110
                    pass
            try:
                loop = asyncio.get_event_loop()
                _bounds_feeder_task = loop.create_task(
                    _bounds_hardware_feeder(laserhead, events, facet=_preview_facet)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Could not start preview feeder task: {exc}")
    else:
        Camera.mock_laser_events = None
        Camera.mock_laser_pattern = None
        Camera.mock_preview_facet = None
        if _bounds_feeder_task is not None:
            try:
                _bounds_feeder_task.cancel()
            except Exception:  # noqa: BLE001, S110
                pass
            _bounds_feeder_task = None
        logger.info("Preview Dots DISABLED")

    return _bounds_active


def is_bounds_active() -> bool:
    """Return whether dot preview is currently active."""
    return _bounds_active


def get_preview_facet() -> int | None:
    """Return currently selected preview facet (0..3 or None for all facets)."""
    return _preview_facet


async def capture_jpeg_with_preview(laserhead, camera):
    """Capture a JPEG snapshot projecting Preview Dots on 1 or all facets."""
    if not _bounds_active:
        return camera.capture()

    events = _build_preview_events(laserhead, num_dots=6)
    Camera.mock_laser_events = events
    Camera.mock_laser_pattern = None
    Camera.mock_preview_facet = _preview_facet

    if constants.ESP32:
        async with _fpga_calib_lock:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=False, polygon=False, synchronize=True
            )
            reps = _calc_exposure_reps(laserhead)

            if _preview_facet is not None:
                true_facet = await laserhead.remap(_preview_facet)
                await _send_events(
                    laserhead, events, steps_line=0, repetitions=reps, facet=true_facet
                )
            else:
                await _send_events(
                    laserhead, events, steps_line=0, repetitions=reps * 4, facet=None
                )

            camera.discard_frame()
            jpeg_data = camera.capture()
            await _send_events(laserhead, None)
            await laserhead.wait_fifo_empty()
            return jpeg_data

    return camera.capture()
