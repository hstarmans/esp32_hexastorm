"""Hexastorm optical calibration controller running on ESP32-S3 and CPython mock.

All shared DSP, 2D centroiding, global rotation rectification, spatial-median shift
computation, 24 MHz dot-grid slicing, and virtual 2D stacking live in
``hexastorm.calibration.core`` (and ``hexastorm.calibration.vector_lite`` for the
optional C99 ``_vector_core`` slicer). This module orchestrates the OV2640 camera
capture, FPGA SPI locking, adaptive exposure timing, task cancellation/progress
status, configuration persistence, and Web UI endpoints.
"""

import asyncio
import gc
import logging
import math
import time

try:
    from typing import TYPE_CHECKING
except ImportError:
    TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Any

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
    summarize_facet_dots_ulab,
)
from hexastorm.calibration.vector_lite import slice_dot_grid_vector_core

from . import constants
from .constants import (
    CALIBRATION_TOLERANCE_UM,
    CONFIG,
    SVGA_PIXEL_SIZE_UM,
    update_config,
)

calibration_ulab = analyze_facets_ulab

__all__ = [
    "CalibrationCancelled",
    "PrismCurveLite",
    "VirtualStackerUlab",
    "analyze_facets_ulab",
    "calculate_facet_shifts_ulab",
    "calibration_ulab",
    "capture_jpeg_with_preview",
    "compute_angle_ulab",
    "finish_calibration_status",
    "get_calibration_status",
    "get_dots_ulab",
    "get_preview_facet",
    "is_bounds_active",
    "is_calibration_running",
    "make_dot_centers_mm",
    "make_tick_dot_events",
    "request_cancel_calibration",
    "rotate_dots_ulab",
    "run_facet_calibration",
    "run_pattern_error",
    "run_scan_visibility",
    "set_bounds_preview",
    "slice_dot_grid_events",
    "slice_dot_grid_vector_core",
    "start_calibration_status",
    "subtract_dark_frame_u8",
    "update_calibration_status",
    "update_custom_facets",
]

logger = logging.getLogger(__name__)


class CalibrationCancelled(Exception):
    """Raised when an operator cancels an active calibration task."""


# Global state for "Preview Dots" / "Show Bounds" preview
_bounds_active = False
_preview_facet = 0  # Default to Facet 0 (F1), or None for all 4 facets
_bounds_feeder_task = None
_fpga_calib_lock = asyncio.Lock()

# Global state for non-blocking calibration observability & cancellation
_calib_cancel_requested = False
_calib_start_ms = 0
_calib_status: "dict[str, Any]" = {
    "running": False,
    "task": None,
    "state": "idle",
    "phase": "",
    "progress_pct": 0,
    "step_current": 0,
    "step_total": 0,
    "message": "",
    "last_capture_ms": 0,
    "avg_capture_ms": 0,
    "elapsed_ms": 0,
    "error": None,
}


def _ticks_ms() -> int:
    """Monotonic millisecond timestamp compatible with both MicroPython and CPython."""
    ticks_fn = getattr(time, "ticks_ms", None)
    if ticks_fn is not None:
        return int(ticks_fn())
    return int(time.monotonic() * 1000)


def _ticks_diff(end_ms: int, start_ms: int) -> int:
    """Elapsed milliseconds between two monotonic ``_ticks_ms`` timestamps."""
    diff_fn = getattr(time, "ticks_diff", None)
    if diff_fn is not None:
        return int(diff_fn(end_ms, start_ms))
    return end_ms - start_ms


def get_calibration_status() -> dict:
    """Return a JSON-safe snapshot of current or most recent calibration task state."""
    snapshot = dict(_calib_status)
    if snapshot["running"] and _calib_start_ms > 0:
        snapshot["elapsed_ms"] = max(0, _ticks_diff(_ticks_ms(), _calib_start_ms))
    return snapshot


def is_calibration_running() -> bool:
    """Return True while a calibration task is actively executing."""
    return bool(_calib_status["running"])


def start_calibration_status(task_name: str, message: str = "") -> None:
    """Mark calibration task state as active and clear any stale cancel request."""
    global _calib_cancel_requested, _calib_start_ms
    _calib_cancel_requested = False
    _calib_start_ms = _ticks_ms()
    _calib_status.update(
        {
            "running": True,
            "task": task_name,
            "state": "running",
            "phase": "starting",
            "progress_pct": 0,
            "step_current": 0,
            "step_total": 0,
            "message": message or f"Starting {task_name}...",
            "last_capture_ms": 0,
            "avg_capture_ms": 0,
            "elapsed_ms": 0,
            "error": None,
        }
    )


def update_calibration_status(**fields) -> None:
    """Update partial calibration status fields and refresh elapsed time."""
    for key, val in fields.items():
        if key == "progress_pct" and val is not None:
            _calib_status[key] = max(0, min(100, int(val)))
        elif key in _calib_status:
            _calib_status[key] = val
    if _calib_start_ms > 0:
        _calib_status["elapsed_ms"] = max(0, _ticks_diff(_ticks_ms(), _calib_start_ms))


def finish_calibration_status(
    state: str, message: str = "", error: "str | None" = None
) -> None:
    """Finalize calibration task status ('done', 'cancelled', or 'error')."""
    global _calib_cancel_requested
    _calib_cancel_requested = False
    elapsed_ms = (
        max(0, _ticks_diff(_ticks_ms(), _calib_start_ms))
        if _calib_start_ms > 0
        else int(_calib_status.get("elapsed_ms", 0))
    )
    _calib_status.update(
        {
            "running": False,
            "state": state,
            "phase": state,
            "progress_pct": 100 if state == "done" else _calib_status["progress_pct"],
            "message": message or _calib_status["message"],
            "elapsed_ms": elapsed_ms,
            "error": error,
        }
    )


def request_cancel_calibration() -> bool:
    """Request graceful cancellation of the currently running calibration task."""
    global _calib_cancel_requested
    if not _calib_status["running"]:
        return False
    _calib_cancel_requested = True
    _calib_status["phase"] = "cancelling"
    _calib_status["message"] = "Cancelling calibration..."
    logger.warning("Calibration cancellation requested by operator.")
    return True


def _begin_calibration_scope(task_name: str, message: str) -> bool:
    """Acquire internal status tracking unless already owned by outer caller."""
    if _calib_status["running"]:
        return False
    start_calibration_status(task_name, message)
    return True


def _check_cancelled() -> None:
    if _calib_cancel_requested:
        raise CalibrationCancelled("Calibration cancelled by user")


def _set_mock_camera(**attrs) -> None:
    """Update CPython mock Camera class attributes; no-op on ESP32 C-type Camera."""
    if constants.ESP32:
        return
    for key, val in attrs.items():
        if hasattr(Camera, key):
            setattr(Camera, key, val)


# Initialize mock camera calibration state from persisted CONFIG
_set_mock_camera(
    mock_calibrated_facets=dict(CONFIG["calibration"]["facets"]),
    mock_apply_correction=bool(CONFIG["calibration"]["apply_correction"]),
)


def _get_optical_context(laserhead) -> "tuple[dict, PrismCurveLite, int]":
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
    bounds: "list[int] | None", scan_len: int, curve: PrismCurveLite
) -> "tuple[float, float]":
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


def _calc_exposure_reps(laserhead, measured_capture_ms: "float | None" = None) -> int:
    """Calculate safe FPGA scanline repeat count covering sensor exposure & readout."""
    rpm = float(laserhead.cfg.laser_timing["rpm"])
    rev_time_ms = 60000.0 / max(600.0, rpm)
    exposure_ms = float(CONFIG["calibration"]["camera_exposure_ms"])
    target_ms = max(exposure_ms * 2.0, float(measured_capture_ms or 0.0)) + 120.0
    return max(12, min(80, math.ceil(target_ms / rev_time_ms)))


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


def _median_list(values: "list[float]") -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 == 1 else 0.5 * (float(s[mid - 1]) + float(s[mid]))


def _capture_dark_frame(camera) -> "tuple[Any, int]":
    """Capture a baseline dark frame and measure sensor acquisition duration in ms."""
    t0 = _ticks_ms()
    if constants.ESP32:
        camera.discard_frame()
    frame = camera.capture_grayscale_array()
    capture_ms = max(0, _ticks_diff(_ticks_ms(), t0))
    return frame, capture_ms


async def project_and_capture_facet(
    laserhead,
    camera,
    events: "list[tuple[int, int]] | None" = None,
    line: "list[int] | None" = None,
    facet: int = 0,
    measured_capture_ms: "float | None" = None,
    timing_out: "dict[str, float | int] | None" = None,
):
    """Project 24 MHz events (or 1D binary list) on one facet and capture a frame."""
    _check_cancelled()
    if events is None and line is not None:
        total_ticks = int(laserhead.cfg.laser_timing["scanline_ticks"])
        events = ulabext.bits_to_events(line, total_ticks)
    events = events or []

    async with _fpga_calib_lock:
        true_facet = await laserhead.remap(facet)

        _set_mock_camera(
            mock_laser_events=events,
            mock_laser_pattern=None,
            mock_active_facet=facet,
            mock_v_stage_mm=0.0,
        )

        if constants.ESP32:
            reps = _calc_exposure_reps(
                laserhead, measured_capture_ms=measured_capture_ms
            )
            await _send_events(
                laserhead, events, steps_line=0, repetitions=reps, facet=true_facet
            )
            t0 = _ticks_ms()
            camera.discard_frame()
            frame = camera.capture_grayscale_array()
            capture_ms = max(0, _ticks_diff(_ticks_ms(), t0))
            await _send_events(laserhead, None)
            await laserhead.wait_fifo_empty()
        else:
            reps = 3
            await _send_events(
                laserhead, events, steps_line=0, repetitions=reps, facet=true_facet
            )
            t0 = _ticks_ms()
            frame = camera.capture_grayscale_array()
            capture_ms = max(0, _ticks_diff(_ticks_ms(), t0))

        if timing_out is not None:
            timing_out["capture_ms"] = capture_ms
            timing_out["reps"] = reps
        return frame


async def run_scan_visibility(
    laserhead,
    camera,
    facet: int = 0,
    step: int = 20,
    fast: bool = True,
    progress_callback=None,
):
    """Scan scanline coordinates to locate sensor FOV bounds (coarse+binary when fast=True)."""
    _params, curve, scan_len = _get_optical_context(laserhead)
    step = max(1, step)
    use_fast = fast and step >= 10 and scan_len > step * 4

    owned_status = _begin_calibration_scope(
        "scan_visibility", f"Scanning visibility (facet F{facet + 1})..."
    )
    t_total_start = _ticks_ms()
    captures = 0
    capture_ms_sum = 0
    avg_capture_ms = 0.0
    probed: "dict[int, bool]" = {}
    coarse_pixels: "list[int]" = []
    linear_pixels: "list[int]" = []

    if use_fast:
        coarse_step = max(step * 2, 50)
        coarse_pixels = list(range(0, scan_len, coarse_step))
        if (scan_len - 1) not in coarse_pixels:
            coarse_pixels.append(scan_len - 1)
        est_total = len(coarse_pixels) + 8
    else:
        linear_pixels = list(range(0, scan_len, step))
        if (scan_len - 1) not in linear_pixels:
            linear_pixels.append(scan_len - 1)
        est_total = max(1, len(linear_pixels))

    logger.info(
        f"Starting Scan Visibility: len={scan_len}, step={step}, "
        f"fast={use_fast}, facet={facet}, est_captures={est_total}"
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
            _check_cancelled()
            # 1. Dark Frame Capture (laser OFF, do not force hardware override pin ON)
            _set_mock_camera(mock_laser_events=None, mock_laser_pattern=None)
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=False)
            await asyncio.sleep(0.05)
            dark_frame, dark_ms = _capture_dark_frame(camera)
            avg_capture_ms = float(dark_ms)
            logger.info(f"Scan Visibility dark frame captured in {dark_ms} ms")

            async def _probe_px(px: int, phase_label: str) -> bool:
                nonlocal captures, capture_ms_sum, avg_capture_ms
                if px in probed:
                    return probed[px]
                _check_cancelled()
                t_start = (px * curve.total_ticks) // max(1, scan_len)
                t_end = min(
                    curve.total_ticks,
                    ((px + 1) * curve.total_ticks) // max(1, scan_len),
                )
                events = [(t_start, 255)]
                if t_end < curve.total_ticks:
                    events.append((max(t_start + 1, t_end), 0))

                t_info: "dict[str, float | int]" = {}
                frame = await project_and_capture_facet(
                    laserhead,
                    camera,
                    events=events,
                    facet=facet,
                    measured_capture_ms=avg_capture_ms,
                    timing_out=t_info,
                )
                dots, metrics = get_dots_ulab(frame, dark_frame=dark_frame)
                is_vis = dots.shape[0] > 0 or metrics["peak_intensity"] >= 40
                probed[px] = is_vis

                cap_ms = int(t_info.get("capture_ms", 0))
                captures += 1
                capture_ms_sum += cap_ms
                avg_capture_ms = capture_ms_sum / captures
                del frame
                gc.collect()

                pct = (
                    99
                    if captures >= est_total
                    else max(1, int((captures / max(1, est_total)) * 100))
                )
                update_calibration_status(
                    phase=phase_label,
                    progress_pct=pct,
                    step_current=captures,
                    step_total=max(captures, est_total),
                    last_capture_ms=cap_ms,
                    avg_capture_ms=round(avg_capture_ms, 1),
                    message=(
                        f"Scan px {px}/{scan_len - 1} "
                        f"({'hit' if is_vis else 'dark'}, {cap_ms} ms/frame)"
                    ),
                )
                if progress_callback:
                    await progress_callback(pct, px)
                return is_vis

            if use_fast:
                for px in coarse_pixels:
                    await _probe_px(px, "coarse_scan")

                vis_coarse = [px for px in coarse_pixels if probed.get(px)]
                refine_tol = max(4, step // 2)
                if vis_coarse:
                    first_vis = min(vis_coarse)
                    last_vis = max(vis_coarse)

                    # Refine left boundary between last dark probe < first_vis and first_vis
                    dark_left = [
                        px
                        for px in coarse_pixels
                        if px < first_vis and not probed.get(px)
                    ]
                    if dark_left:
                        lo = max(dark_left)
                        hi = first_vis
                        while (hi - lo) > refine_tol:
                            mid = (lo + hi) // 2
                            if mid == lo or mid == hi:
                                break
                            if await _probe_px(mid, "refine_left"):
                                hi = mid
                            else:
                                lo = mid

                    # Refine right boundary between last_vis and first dark probe > last_vis
                    dark_right = [
                        px
                        for px in coarse_pixels
                        if px > last_vis and not probed.get(px)
                    ]
                    if dark_right:
                        lo = last_vis
                        hi = min(dark_right)
                        while (hi - lo) > refine_tol:
                            mid = (lo + hi) // 2
                            if mid == lo or mid == hi:
                                break
                            if await _probe_px(mid, "refine_right"):
                                lo = mid
                            else:
                                hi = mid
            else:
                for px in linear_pixels:
                    await _probe_px(px, "linear_scan")

        visible_indices = [px for px, is_vis in probed.items() if is_vis]
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

        total_ms = max(0, _ticks_diff(_ticks_ms(), t_total_start))
        timing = {
            "total_ms": total_ms,
            "captures": captures,
            "avg_capture_ms": round(avg_capture_ms, 1),
        }
        msg = (
            f"Visible px {bounds[0]}..{bounds[1]} ({percentage}%, {captures} frames in {total_ms / 1000.0:.1f}s)"
            if bounds
            else f"No laser signal in FOV ({captures} frames in {total_ms / 1000.0:.1f}s)"
        )
        if owned_status:
            finish_calibration_status("done", msg)

        logger.info(
            f"Scan Visibility complete: visible_pixels={bounds} "
            f"({visible_count}/{scan_len} px, {percentage}%), timing={timing}"
        )

        return {
            "status": "success",
            "facet": facet,
            "step": step,
            "fast": use_fast,
            "scanline_length": scan_len,
            "visible_pixels": bounds,
            "count": visible_count,
            "percentage": percentage,
            "timing": timing,
        }
    except CalibrationCancelled as exc:
        if owned_status:
            finish_calibration_status("cancelled", str(exc))
        raise
    except Exception as exc:
        if owned_status:
            finish_calibration_status(
                "error", f"Scan visibility failed: {exc}", str(exc)
            )
        raise
    finally:
        _set_mock_camera(mock_laser_events=None, mock_laser_pattern=None)


def _read_period_means(laserhead, shift: int, num_facets: int) -> "list[float]":
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
    capture_ms_sum = 0
    avg_capture_ms = 0.0
    t_total_start = _ticks_ms()
    owned_status = _begin_calibration_scope(
        "calibrate", "Starting multi-facet 1D calibration..."
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
            _check_cancelled()
            _set_mock_camera(mock_laser_events=None, mock_laser_pattern=None)
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=False)
            await asyncio.sleep(0.05)
            dark_frame, dark_ms = _capture_dark_frame(camera)
            avg_capture_ms = float(dark_ms)

            for it_idx in range(iterations):
                raw_dots = []
                spot_metrics = []
                for facet in range(num_facets):
                    _check_cancelled()
                    t_info: "dict[str, float | int]" = {}
                    frame = await project_and_capture_facet(
                        laserhead,
                        camera,
                        events=base_events,
                        facet=facet,
                        measured_capture_ms=avg_capture_ms,
                        timing_out=t_info,
                    )
                    dots, metrics = get_dots_ulab(
                        frame, dark_frame=dark_frame, pixelsize=SVGA_PIXEL_SIZE_UM
                    )
                    del frame
                    gc.collect()
                    if dots.shape[0] < 2:
                        raise ValueError(
                            f"Facet {facet}: insufficient calibration dots detected ({dots.shape[0]})."
                        )
                    raw_dots.append(dots)
                    spot_metrics.append(metrics)

                    done_captures += 1
                    cap_ms = int(t_info.get("capture_ms", 0))
                    capture_ms_sum += cap_ms
                    avg_capture_ms = capture_ms_sum / done_captures
                    pct = int((done_captures / total_captures) * 100)
                    update_calibration_status(
                        phase="capture_1d",
                        progress_pct=pct,
                        step_current=done_captures,
                        step_total=total_captures,
                        last_capture_ms=cap_ms,
                        avg_capture_ms=round(avg_capture_ms, 1),
                        message=(
                            f"1D iter {it_idx + 1}/{iterations} F{facet + 1} "
                            f"({cap_ms} ms/frame)"
                        ),
                    )
                    if progress_callback:
                        await progress_callback(pct, facet)

                res = summarize_facet_dots_ulab(
                    raw_dots,
                    spot_metrics,
                    pixelsize=SVGA_PIXEL_SIZE_UM,
                    compute_rotation=True,
                )
                all_iter_results.append(res)
                logger.info(
                    f"1D calibration iter {it_idx + 1}/{iterations}: "
                    f"angle={res['0']['global_angle_deg']} deg, avg_cap={avg_capture_ms:.1f} ms"
                )
                gc.collect()

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
        _set_mock_camera(
            mock_calibrated_facets=facets_cfg,
            mock_apply_correction=True,
        )
        update_config()

        total_ms = max(0, _ticks_diff(_ticks_ms(), t_total_start))
        timing = {
            "total_ms": total_ms,
            "captures": done_captures,
            "avg_capture_ms": round(avg_capture_ms, 1),
        }
        if owned_status:
            finish_calibration_status(
                "done",
                f"Facet calibration saved ({done_captures} frames in {total_ms / 1000.0:.1f}s)",
            )

        return {
            "status": "success",
            "iterations": iterations,
            "facets": facets_cfg,
            "timing": timing,
        }
    except CalibrationCancelled as exc:
        if owned_status:
            finish_calibration_status("cancelled", str(exc))
        raise
    except Exception as exc:
        if owned_status:
            finish_calibration_status(
                "error", f"Facet calibration failed: {exc}", str(exc)
            )
        raise
    finally:
        _set_mock_camera(mock_laser_events=None, mock_laser_pattern=None)


async def run_pattern_error(
    laserhead,
    camera,
    num_dots: int = 6,
    calibrate_first: bool = True,
    exposures_2d: int = 2,
    verify_before_2d: bool = False,
    slicer: str = "analytical",
    progress_callback=None,
):
    """Execute closed-loop 2D Verified Calibration with adaptive capture timing.

    Modes:
    - ``verify_before_2d=False`` (fast default, ~20 captures): measures the
      uncorrected baseline in a 4-capture 1D pass across all facets and runs the
      full 2D ``VirtualStackerUlab`` pass on the corrected pattern.
    - ``verify_before_2d=True`` (full 2D Before+After comparison): also stacks a
      2D uncorrected pass for Before errors.
    - ``slicer="analytical"`` uses ``slice_dot_grid_events``; ``slicer="vector_core"``
      slices dot polygons through the C99 ``_vector_core`` SPI kernel.
    """
    if slicer not in ("analytical", "vector_core"):
        raise ValueError(f"Unsupported calibration slicer: {slicer}")

    params, curve, scan_len = _get_optical_context(laserhead)
    num_facets = int(laserhead.cfg.laser_timing["facets"])
    steps_mm = _get_orth_steps_mm(laserhead)
    exposures_2d = max(1, exposures_2d)
    dot_diameter_mm = 0.08
    bounds = CONFIG["calibration"]["visible_pixels"]
    u_min, u_max = _bounds_to_u_range(bounds, scan_len, curve)

    owned_status = _begin_calibration_scope(
        "pattern_error", f"Starting 2D calibration ({slicer}, exp={exposures_2d})..."
    )
    t_total_start = _ticks_ms()
    captures = 0
    capture_ms_sum = 0
    avg_capture_ms = 0.0

    try:
        if constants.ESP32:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=False, polygon=False, synchronize=True
            )

        with camera.analysis_mode(
            frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
        ):
            _check_cancelled()
            # 1. Dark Frame
            _set_mock_camera(mock_laser_events=None, mock_laser_pattern=None)
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=False)
            await asyncio.sleep(0.05)
            dark_frame, dark_ms = _capture_dark_frame(camera)
            avg_capture_ms = float(dark_ms)
            logger.info(
                f"Pattern Error started: dark={dark_ms} ms, slicer={slicer}, "
                f"exposures_2d={exposures_2d}, verify_before_2d={verify_before_2d}"
            )

            shift = await laserhead.remap(0)
            period_means = _read_period_means(laserhead, shift, num_facets)
            facets_cfg = dict(CONFIG["calibration"].get("facets") or {})

            async def _capture_1d_baseline(
                phase_name: str, progress_base: int, progress_span: int
            ) -> dict:
                nonlocal captures, capture_ms_sum, avg_capture_ms
                centers_1d = make_dot_centers_mm(
                    u_min, u_max, num_dots=num_dots, margin_mm=0.25
                )
                events_1d = make_tick_dot_events(
                    centers_1d, dot_width_mm=0.02, curve=curve
                )
                raw_dots = []
                spot_metrics = []
                for facet in range(num_facets):
                    _check_cancelled()
                    t_info: "dict[str, float | int]" = {}
                    frame = await project_and_capture_facet(
                        laserhead,
                        camera,
                        events=events_1d,
                        facet=facet,
                        measured_capture_ms=avg_capture_ms,
                        timing_out=t_info,
                    )
                    dots, metrics = get_dots_ulab(
                        frame, dark_frame=dark_frame, pixelsize=SVGA_PIXEL_SIZE_UM
                    )
                    del frame
                    gc.collect()
                    if dots.shape[0] < 2:
                        raise ValueError(
                            f"Facet {facet}: insufficient calibration dots detected ({dots.shape[0]})."
                        )
                    raw_dots.append(dots)
                    spot_metrics.append(metrics)

                    cap_ms = int(t_info.get("capture_ms", 0))
                    captures += 1
                    capture_ms_sum += cap_ms
                    avg_capture_ms = capture_ms_sum / captures
                    pct = progress_base + int(
                        ((facet + 1) / num_facets) * progress_span
                    )
                    update_calibration_status(
                        phase=phase_name,
                        progress_pct=pct,
                        step_current=facet + 1,
                        step_total=num_facets,
                        last_capture_ms=cap_ms,
                        avg_capture_ms=round(avg_capture_ms, 1),
                        message=f"1D baseline F{facet + 1}/{num_facets} ({cap_ms} ms/frame)",
                    )
                    if progress_callback:
                        await progress_callback(pct, facet)

                return summarize_facet_dots_ulab(
                    raw_dots,
                    spot_metrics,
                    pixelsize=SVGA_PIXEL_SIZE_UM,
                    compute_rotation=True,
                )

            def _slice_2d(offsets_mm: "dict[int, tuple[float, float]]") -> dict:
                if slicer == "vector_core":
                    return slice_dot_grid_vector_core(
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
                return slice_dot_grid_events(
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

            async def _run_2d_pass(
                offsets_mm: "dict[int, tuple[float, float]]",
                phase_name: str,
                progress_base: int,
                progress_span: int,
            ) -> dict:
                nonlocal captures, capture_ms_sum, avg_capture_ms
                plan = _slice_2d(offsets_mm)
                total_rows = plan["total_rows"]
                active_rows = plan["rows"]
                gc.collect()
                # Reuse one single stacker buffer across facets (75% less peak heap)
                stacker = VirtualStackerUlab(
                    expected_total_steps=total_rows,
                    exposures=exposures_2d,
                    px_size_um=SVGA_PIXEL_SIZE_UM,
                    steps_mm=steps_mm,
                    rotation_angle_deg=global_angle,
                    frame_shape=dark_frame.shape if dark_frame is not None else None,
                )
                rows_by_facet = [
                    [item for item in active_rows if item[1] == f]
                    for f in range(num_facets)
                ]
                n_active = max(1, len(active_rows))
                done_rows = 0
                raw_dots = []
                spot_metrics = []
                try:
                    for facet in range(num_facets):
                        stacker.reset()
                        for row_idx, _f, events in rows_by_facet[facet]:
                            _check_cancelled()
                            t_info: "dict[str, float | int]" = {}
                            frame = await project_and_capture_facet(
                                laserhead,
                                camera,
                                events=events,
                                facet=facet,
                                measured_capture_ms=avg_capture_ms,
                                timing_out=t_info,
                            )
                            stacker.add_exposure(
                                frame, num_steps=row_idx, dark_frame=dark_frame
                            )
                            cap_ms = int(t_info.get("capture_ms", 0))
                            captures += 1
                            capture_ms_sum += cap_ms
                            avg_capture_ms = capture_ms_sum / captures
                            del frame
                            gc.collect()

                            done_rows += 1
                            pct = progress_base + int(
                                (done_rows / n_active) * progress_span
                            )
                            update_calibration_status(
                                phase=phase_name,
                                progress_pct=pct,
                                step_current=done_rows,
                                step_total=len(active_rows),
                                last_capture_ms=cap_ms,
                                avg_capture_ms=round(avg_capture_ms, 1),
                                message=(
                                    f"2D {phase_name} row {done_rows}/{len(active_rows)} "
                                    f"F{facet + 1} ({cap_ms} ms/frame)"
                                ),
                            )
                            if progress_callback:
                                await progress_callback(pct, facet)

                        stacked_frame = stacker.get_result()
                        if stacked_frame is None:
                            raise ValueError(f"Facet {facet}: no 2D rows captured.")
                        dots, metrics = get_dots_ulab(
                            stacked_frame,
                            dark_frame=None,
                            pixelsize=SVGA_PIXEL_SIZE_UM,
                            max_diameter_um=320.0,
                        )
                        if dots.shape[0] < 2:
                            raise ValueError(
                                f"Facet {facet}: insufficient calibration dots detected ({dots.shape[0]})."
                            )
                        raw_dots.append(dots)
                        spot_metrics.append(metrics)
                finally:
                    del stacker
                    gc.collect()

                return summarize_facet_dots_ulab(
                    raw_dots,
                    spot_metrics,
                    pixelsize=SVGA_PIXEL_SIZE_UM,
                    compute_rotation=False,
                    rotation_angle_deg=global_angle,
                )

            need_1d_calib = calibrate_first or len(facets_cfg) < num_facets
            if not verify_before_2d:
                # Fast path: single 1D uncorrected pass -> Before baseline (+ calibration),
                # then full 2D VirtualStackerUlab verification on the corrected pattern.
                res_1d = await _capture_1d_baseline(
                    "baseline_1d", progress_base=0, progress_span=25
                )
                before_res = res_1d
                if need_1d_calib:
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
                else:
                    rot_angles = [
                        float(facets_cfg[str(f)].get("rotation_deg", 0.0))
                        for f in range(num_facets)
                        if str(f) in facets_cfg
                    ]
                    global_angle = (
                        _median_list(rot_angles)
                        if rot_angles
                        else float(res_1d["0"]["global_angle_deg"])
                    )

                corr_offsets = {
                    f: (
                        float(facets_cfg[str(f)]["scan"]),
                        float(facets_cfg[str(f)]["orth"]),
                    )
                    for f in range(num_facets)
                }
                after_res = await _run_2d_pass(
                    corr_offsets,
                    phase_name="verify_2d",
                    progress_base=25,
                    progress_span=75,
                )
            else:
                # Full dual-2D comparison path
                if need_1d_calib:
                    res_1d = await _capture_1d_baseline(
                        "baseline_1d", progress_base=0, progress_span=15
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
                    b_base, b_span, a_base, a_span = 15, 40, 55, 45
                else:
                    rot_angles = [
                        float(facets_cfg[str(f)].get("rotation_deg", 0.0))
                        for f in range(num_facets)
                        if str(f) in facets_cfg
                    ]
                    global_angle = _median_list(rot_angles) if rot_angles else 0.0
                    b_base, b_span, a_base, a_span = 0, 50, 50, 50

                zero_offsets = {f: (0.0, 0.0) for f in range(num_facets)}
                before_res = await _run_2d_pass(
                    zero_offsets,
                    phase_name="before_2d",
                    progress_base=b_base,
                    progress_span=b_span,
                )

                corr_offsets = {
                    f: (
                        float(facets_cfg[str(f)]["scan"]),
                        float(facets_cfg[str(f)]["orth"]),
                    )
                    for f in range(num_facets)
                }
                after_res = await _run_2d_pass(
                    corr_offsets,
                    phase_name="after_2d",
                    progress_base=a_base,
                    progress_span=a_span,
                )

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

        total_ms = max(0, _ticks_diff(_ticks_ms(), t_total_start))
        timing = {
            "total_ms": total_ms,
            "captures": captures,
            "avg_capture_ms": round(avg_capture_ms, 1),
        }

        report = {
            "status": "success",
            "passed": passed,
            "tolerance_um": CALIBRATION_TOLERANCE_UM,
            "max_error_before_um": max_err_before,
            "max_error_after_um": max_err_after,
            "mae_before_um": mae_before,
            "mae_after_um": mae_after,
            "slicer": slicer,
            "exposures_2d": exposures_2d,
            "verify_before_2d": verify_before_2d,
            "timing": timing,
            "facets": per_facet_report,
        }

        if "calibration" not in CONFIG:
            CONFIG["calibration"] = {}
        CONFIG["calibration"]["facets"] = facets_cfg
        CONFIG["calibration"]["last_report"] = report
        CONFIG["calibration"]["apply_correction"] = True
        _set_mock_camera(
            mock_calibrated_facets=facets_cfg,
            mock_apply_correction=True,
        )
        update_config()

        if owned_status:
            finish_calibration_status(
                "done",
                (
                    f"2D Verified ({'PASS' if passed else 'FAIL'}: "
                    f"{max_err_after} um, {captures} frames in {total_ms / 1000.0:.1f}s)"
                ),
            )

        logger.info(
            f"Verified Calibration complete: passed={passed}, slicer={slicer}, "
            f"max_before={max_err_before} um -> max_after={max_err_after} um, timing={timing}"
        )
        return report
    except CalibrationCancelled as exc:
        if owned_status:
            finish_calibration_status("cancelled", str(exc))
        raise
    except Exception as exc:
        if owned_status:
            finish_calibration_status(
                "error", f"2D calibration failed: {exc}", str(exc)
            )
        raise
    finally:
        _set_mock_camera(mock_laser_events=None, mock_laser_pattern=None)


def update_custom_facets(
    facets_override: "dict | list | None" = None,
    apply_correction: "bool | None" = None,
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

    apply_flag = bool(CONFIG["calibration"]["apply_correction"])
    _set_mock_camera(
        mock_calibrated_facets=facets_cfg,
        mock_apply_correction=apply_flag,
    )
    update_config()
    return {
        "status": "success",
        "apply_correction": apply_flag,
        "facets": facets_cfg,
        "last_report": last_rep,
    }


async def _bounds_hardware_feeder(
    laserhead, events: "list[tuple[int, int]]", facet: "int | None" = None
):
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


def _build_preview_events(laserhead, num_dots: int = 6) -> "list[tuple[int, int]]":
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
    enabled: "bool | None" = None,
    facet: "int | str | None" = "keep",
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
        _set_mock_camera(
            mock_laser_events=events,
            mock_laser_pattern=None,
            mock_preview_facet=_preview_facet,
        )
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
        _set_mock_camera(
            mock_laser_events=None,
            mock_laser_pattern=None,
            mock_preview_facet=None,
        )
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


def get_preview_facet() -> "int | None":
    """Return currently selected preview facet (0..3 or None for all facets)."""
    return _preview_facet


async def capture_jpeg_with_preview(laserhead, camera):
    """Capture a JPEG snapshot projecting Preview Dots on 1 or all facets."""
    if not _bounds_active:
        return camera.capture()

    events = _build_preview_events(laserhead, num_dots=6)
    _set_mock_camera(
        mock_laser_events=events,
        mock_laser_pattern=None,
        mock_preview_facet=_preview_facet,
    )

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
