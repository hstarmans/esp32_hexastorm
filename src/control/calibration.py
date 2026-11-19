"""Hexastorm optical calibration module running on ESP32-S3 and CPython mock.

Performs dark-frame subtracted signal analysis using ulab.numpy to determine:
- Scanline visibility bounds [lower_pixel, upper_pixel]
- Bounds preview visualization ("Show Bounds")
- Sub-pixel 2D dot centroid detection & spot metrics (get_dots_ulab)
- 2D rotation rectification & facet shift calculation (calibration_ulab)
- Closed-loop facet calibration & Verified Calibration (run_pattern_error)
"""

import asyncio
import gc
import logging
import math

try:
    from ulab import numpy as np
except ImportError:
    import numpy as np

from camera import Camera, FrameSize, PixelFormat

from . import constants
from .constants import (
    CALIBRATION_TOLERANCE_UM,
    CONFIG,
    SVGA_PIXEL_SIZE_UM,
    update_config,
)

logger = logging.getLogger(__name__)

NP_FLOAT = getattr(np, "float", float)

# Global state for "Preview Dots" / "Show Bounds" preview
_bounds_active = False
_preview_facet = 0  # Default to Facet 0 (F1), or None for all 4 facets
_bounds_feeder_task = None

# Initialize mock camera calibration state from persisted CONFIG
Camera.mock_calibrated_facets = dict(CONFIG["calibration"]["facets"])
Camera.mock_apply_correction = bool(CONFIG["calibration"]["apply_correction"])


def subtract_dark_frame_u8(img, dark_frame=None):
    """Clamped uint8 dark-frame subtraction without int16/int32 allocation."""
    if dark_frame is not None and dark_frame.shape == img.shape:
        return np.maximum(img, dark_frame) - dark_frame
    return img


def get_dots_ulab(
    img,
    dark_frame=None,
    pixelsize: float = SVGA_PIXEL_SIZE_UM,
    min_diameter_um: float = 10.0,
    max_diameter_um: float = 250.0,
    threshold: int | None = None,
    gap_tol: int = 2,
):
    """Detect laser spots and compute sub-pixel intensity-weighted centroids using ulab.

    Uses 1D vector reductions (np.max along rows/cols) to isolate active regions,
    followed by 2D Run-Length Connected Component Labeling (RLE-CCL) with gap bridging
    (equivalent to morphological closing) and 2nd-moment spot shape analysis.

    Returns:
        tuple: (centroids_array, metrics_dict)
            - centroids_array: ulab/numpy float array of shape (N, 2) sorted by X
            - metrics_dict: {
                'mean_spot_size_um', 'mean_eccentricity',
                'num_dots', 'peak_intensity'
              }
    """
    clean = subtract_dark_frame_u8(img, dark_frame)
    row_max = np.max(clean, axis=1)
    peak = int(np.max(row_max))

    empty_metrics = {
        "mean_spot_size_um": 0.0,
        "mean_eccentricity": 0.0,
        "num_dots": 0,
        "peak_intensity": peak,
    }
    if peak < 20:
        return np.zeros((0, 2), dtype=NP_FLOAT), empty_metrics

    thresh = int(threshold) if threshold is not None else max(25, int(peak * 0.45))
    col_max = np.max(clean, axis=0)

    active_rows = [y for y in range(clean.shape[0]) if int(row_max[y]) >= thresh]
    active_cols = [x for x in range(clean.shape[1]) if int(col_max[x]) >= thresh]
    if not active_rows or not active_cols:
        return np.zeros((0, 2), dtype=NP_FLOAT), empty_metrics

    x_min = max(0, active_cols[0] - 1)
    x_max = min(clean.shape[1] - 1, active_cols[-1] + 1)

    # Component table:
    # cid -> [area, w_sum, wx_sum, wy_sum, wxx_sum, wyy_sum, wxy_sum, parent_cid]
    comps = {}
    next_cid = 0
    recent_rows = []  # list of (y, runs_list) where runs_list has [x0, x1, cid]

    def find_root(cid):
        while comps[cid][7] != cid:
            comps[cid][7] = comps[comps[cid][7]][7]
            cid = comps[cid][7]
        return cid

    def union_cids(cid_a, cid_b):
        ra = find_root(cid_a)
        rb = find_root(cid_b)
        if ra == rb:
            return ra
        if ra > rb:
            ra, rb = rb, ra
        ca = comps[ra]
        cb = comps[rb]
        ca[0] += cb[0]
        ca[1] += cb[1]
        ca[2] += cb[2]
        ca[3] += cb[3]
        ca[4] += cb[4]
        ca[5] += cb[5]
        ca[6] += cb[6]
        cb[7] = ra
        return ra

    for y in active_rows:
        recent_rows = [item for item in recent_rows if (y - item[0]) <= gap_tol]
        row_slice = memoryview(clean[y, x_min : x_max + 1])

        runs = []
        in_run = False
        r_start = 0
        ra = 0
        rw = 0.0
        rwx = 0.0
        rwy = 0.0
        rwxx = 0.0
        rwyy = 0.0
        rwxy = 0.0
        fy = float(y)

        for idx, val in enumerate(row_slice):
            if val >= thresh:
                x = x_min + idx
                fx = float(x)
                fv = float(val)
                if not in_run:
                    in_run = True
                    r_start = x
                    ra = 1
                    rw = fv
                    rwx = fv * fx
                    rwy = fv * fy
                    rwxx = fv * fx * fx
                    rwyy = fv * fy * fy
                    rwxy = fv * fx * fy
                else:
                    ra += 1
                    rw += fv
                    rwx += fv * fx
                    rwy += fv * fy
                    rwxx += fv * fx * fx
                    rwyy += fv * fy * fy
                    rwxy += fv * fx * fy
            elif in_run:
                in_run = False
                r_end = x_min + idx - 1
                if runs and (r_start - runs[-1][1]) <= gap_tol:
                    prev = runs[-1]
                    prev[1] = r_end
                    prev[2] += ra
                    prev[3] += rw
                    prev[4] += rwx
                    prev[5] += rwy
                    prev[6] += rwxx
                    prev[7] += rwyy
                    prev[8] += rwxy
                else:
                    runs.append(
                        [r_start, r_end, ra, rw, rwx, rwy, rwxx, rwyy, rwxy, -1]
                    )

        if in_run:
            r_end = x_max
            if runs and (r_start - runs[-1][1]) <= gap_tol:
                prev = runs[-1]
                prev[1] = r_end
                prev[2] += ra
                prev[3] += rw
                prev[4] += rwx
                prev[5] += rwy
                prev[6] += rwxx
                prev[7] += rwyy
                prev[8] += rwxy
            else:
                runs.append([r_start, r_end, ra, rw, rwx, rwy, rwxx, rwyy, rwxy, -1])

        cur_row_runs = []
        for r in runs:
            x0, x1, ra, rw, rwx, rwy, rwxx, rwyy, rwxy, _ = r
            matched_root = -1
            for _, prev_runs in recent_rows:
                for pr in prev_runs:
                    if pr[0] - gap_tol <= x1 and pr[1] + gap_tol >= x0:
                        proot = find_root(pr[2])
                        if matched_root < 0:
                            matched_root = proot
                        else:
                            matched_root = union_cids(matched_root, proot)

            if matched_root < 0:
                cid = next_cid
                next_cid += 1
                comps[cid] = [ra, rw, rwx, rwy, rwxx, rwyy, rwxy, cid]
                matched_root = cid
            else:
                c = comps[matched_root]
                c[0] += ra
                c[1] += rw
                c[2] += rwx
                c[3] += rwy
                c[4] += rwxx
                c[5] += rwyy
                c[6] += rwxy

            cur_row_runs.append([x0, x1, matched_root])

        recent_rows.append((y, cur_row_runs))

    min_radius_px = (min_diameter_um / 2.0) / pixelsize
    max_radius_px = (max_diameter_um / 2.0) / pixelsize
    min_area = max(3, int(math.pi * (min_radius_px**2)))
    max_area = max(min_area + 10, int(math.pi * (max_radius_px**2)))

    valid_dots = []
    spot_sizes_um = []
    eccentricities = []

    for cid, c in comps.items():
        if c[7] != cid:
            continue
        area, w_sum, wx_sum, wy_sum, wxx_sum, wyy_sum, wxy_sum, _ = c
        if area < min_area or area > max_area or w_sum <= 0:
            continue

        cx = wx_sum / w_sum
        cy = wy_sum / w_sum
        valid_dots.append((cx, cy))

        # Equivalent circular diameter in micrometers
        area_um2 = area * (pixelsize**2)
        equiv_diam_um = 2.0 * math.sqrt(area_um2 / math.pi)
        spot_sizes_um.append(equiv_diam_um)

        # 2nd central moments for eccentricity
        mu20 = max(0.0, (wxx_sum / w_sum) - (cx * cx))
        mu02 = max(0.0, (wyy_sum / w_sum) - (cy * cy))
        mu11 = (wxy_sum / w_sum) - (cx * cy)
        common = math.sqrt(((mu20 - mu02) ** 2) + 4.0 * (mu11**2))
        l1 = 0.5 * (mu20 + mu02 + common)
        l2 = max(0.0, 0.5 * (mu20 + mu02 - common))
        if l1 > 1e-9:
            ecc = math.sqrt(max(0.0, 1.0 - (l2 / l1)))
        else:
            ecc = 0.0
        eccentricities.append(ecc)

    if not valid_dots:
        return np.zeros((0, 2), dtype=NP_FLOAT), empty_metrics

    if len(valid_dots) > 1:
        xs = [pt[0] for pt in valid_dots]
        ys = [pt[1] for pt in valid_dots]
        if (max(ys) - min(ys)) > (max(xs) - min(xs)):
            valid_dots.sort(key=lambda pt: pt[1])
        else:
            valid_dots.sort(key=lambda pt: pt[0])

    dots_arr = np.array(valid_dots, dtype=NP_FLOAT)
    metrics = {
        "mean_spot_size_um": round(sum(spot_sizes_um) / len(spot_sizes_um), 2),
        "mean_eccentricity": round(sum(eccentricities) / len(eccentricities), 3),
        "num_dots": len(valid_dots),
        "peak_intensity": peak,
    }
    return dots_arr, metrics


def compute_angle_ulab(dots) -> float:
    """Compute scanline tilt angle in degrees via 1D linear regression in ulab."""
    if len(dots.shape) != 2 or dots.shape[0] < 2:
        return 0.0
    x = dots[:, 0]
    y = dots[:, 1]
    xm = float(np.mean(x))
    ym = float(np.mean(y))
    dx = x - xm
    dy = y - ym
    sum_dx2 = float(np.sum(dx * dx))
    sum_dy2 = float(np.sum(dy * dy))
    if sum_dy2 > sum_dx2:
        if abs(sum_dy2) < 1e-12:
            return 90.0
        slope_x = float(np.sum(dy * dx)) / sum_dy2
        return math.degrees(math.atan2(1.0, slope_x))
    if abs(sum_dx2) < 1e-12:
        return 0.0
    slope = float(np.sum(dx * dy)) / sum_dx2
    return math.degrees(math.atan(slope))


def rotate_dots_ulab(dots, angle_deg: float):
    """Rotate 2D dot coordinates (N, 2) by -angle_deg around the origin."""
    if len(dots.shape) != 2 or dots.shape[0] == 0:
        return np.zeros((0, 2), dtype=NP_FLOAT)
    n = dots.shape[0]
    theta = math.radians(-float(angle_deg))
    c = math.cos(theta)
    s = math.sin(theta)
    x = dots[:, 0]
    y = dots[:, 1]
    out = np.zeros((n, 2), dtype=NP_FLOAT)
    out[:, 0] = x * c - y * s
    out[:, 1] = x * s + y * c
    return out


def calculate_facet_shifts_ulab(
    rect_dots_list,
    pixelsize: float = SVGA_PIXEL_SIZE_UM,
    max_shift_um: float = 250.0,
):
    """Calculate per-facet (scan_shift_um, orth_shift_um) relative to the facet mean."""
    num_facets = len(rect_dots_list)
    if num_facets == 0:
        raise ValueError("No facet dot arrays provided.")

    n_dots = rect_dots_list[0].shape[0]
    if n_dots == 0:
        raise ValueError("Zero dots detected on facet 0.")

    for idx, d in enumerate(rect_dots_list):
        if d.shape[0] != n_dots:
            raise ValueError(
                f"Dot count mismatch on facet {idx}: "
                f"expected {n_dots}, got {d.shape[0]}"
            )

    avg_dots = np.zeros((n_dots, 2), dtype=NP_FLOAT)
    for d in rect_dots_list:
        avg_dots = avg_dots + d
    avg_dots = avg_dots / float(num_facets)

    shifts = np.zeros((num_facets, 2), dtype=NP_FLOAT)
    for idx, d in enumerate(rect_dots_list):
        diff_um = (avg_dots - d) * float(pixelsize)
        shift_scan = float(np.mean(diff_um[:, 0]))
        shift_orth = float(np.mean(diff_um[:, 1]))
        total_shift = math.sqrt(shift_scan * shift_scan + shift_orth * shift_orth)
        if total_shift > max_shift_um:
            raise ValueError(
                f"Facet {idx} shift {total_shift:.1f} um exceeds "
                f"max_shift {max_shift_um} um"
            )
        shifts[idx, 0] = shift_scan
        shifts[idx, 1] = shift_orth

    return shifts


def calibration_ulab(
    facet_frames,
    dark_frame=None,
    pixelsize: float = SVGA_PIXEL_SIZE_UM,
    compute_rotation: bool = True,
    min_diameter_um: float = 10.0,
    max_diameter_um: float = 250.0,
    max_shift_um: float = 250.0,
    orth_virtual_offsets_um=None,
):
    """Analyze 4 facet grayscale frames in RAM and return shifts and metrics."""
    rect_dots_list = []
    angles = []
    spot_metrics_list = []

    for idx, frame in enumerate(facet_frames):
        dots, metrics = get_dots_ulab(
            frame,
            dark_frame=dark_frame,
            pixelsize=pixelsize,
            min_diameter_um=min_diameter_um,
            max_diameter_um=max_diameter_um,
        )
        if dots.shape[0] < 2:
            raise ValueError(
                f"Facet {idx}: insufficient calibration dots "
                f"detected ({dots.shape[0]})."
            )

        angle = compute_angle_ulab(dots) if compute_rotation else 0.0
        rect_dots = rotate_dots_ulab(dots, angle)

        # Virtual orthogonal offset (for stationary real hardware verification)
        if orth_virtual_offsets_um and idx < len(orth_virtual_offsets_um):
            rect_dots[:, 1] = rect_dots[:, 1] + (
                float(orth_virtual_offsets_um[idx]) / float(pixelsize)
            )

        rect_dots_list.append(rect_dots)
        angles.append(angle)
        spot_metrics_list.append(metrics)

    shifts = calculate_facet_shifts_ulab(
        rect_dots_list, pixelsize=pixelsize, max_shift_um=max_shift_um
    )

    results = {}
    for idx in range(len(facet_frames)):
        s_um = float(shifts[idx, 0])
        o_um = float(shifts[idx, 1])
        results[str(idx)] = {
            "Scan_shift_um": round(s_um, 2),
            "Orth_shift_um": round(o_um, 2),
            "Total_error_um": round(math.sqrt(s_um * s_um + o_um * o_um), 2),
            "rotation_angle_deg": round(float(angles[idx]), 4),
            "mean_spot_size_um": spot_metrics_list[idx]["mean_spot_size_um"],
            "mean_eccentricity": spot_metrics_list[idx]["mean_eccentricity"],
            "num_dots": spot_metrics_list[idx]["num_dots"],
        }

    return results


def make_dot_pattern(
    scan_len: int,
    bounds: list[int] | None = None,
    num_dots: int = 6,
    dot_width: int = 2,
    margin: int = 25,
) -> list[int]:
    """Generate a 1D scanline pattern with evenly spaced calibration dots."""
    if not bounds or len(bounds) != 2:
        lower = int(scan_len * 0.25)
        upper = int(scan_len * 0.75)
    else:
        lower, upper = int(bounds[0]), int(bounds[1])

    start_px = min(lower + margin, upper)
    end_px = max(lower, upper - margin)
    span = max(1, end_px - start_px)

    line = [0] * scan_len
    if num_dots <= 1:
        centers = [start_px + span // 2]
    else:
        centers = [
            round(start_px + (i * span) / (num_dots - 1)) for i in range(num_dots)
        ]

    for c in centers:
        for offset in range(dot_width):
            px = c + offset - (dot_width // 2)
            if 0 <= px < scan_len:
                line[px] = 1
    return line


def shift_pattern_along_scan(
    line: list[int],
    scan_corr_um: float,
    scan_len: int,
    lanewidth_mm: float,
) -> list[int]:
    """Shift a 1D binary scanline pattern by scan_corr_um along the scan axis."""
    if abs(scan_corr_um) < 1e-6:
        return list(line)
    um_per_px = (lanewidth_mm * 1000.0) / max(1, scan_len)
    px_shift = round(scan_corr_um / um_per_px)
    if px_shift == 0:
        return list(line)
    shifted = [0] * scan_len
    for idx, val in enumerate(line):
        if val:
            new_idx = idx + px_shift
            if 0 <= new_idx < scan_len:
                shifted[new_idx] = 1
    return shifted


async def project_and_capture_facet(
    laserhead,
    camera,
    line: list[int],
    facet: int = 0,
    scan_corr_um: float = 0.0,
    orth_corr_um: float = 0.0,
):
    """Synchronously project a pattern on a single polygon facet.

    Hardware synchronization protocol (Step 5):
    1. Remaps logical facet index via photodiode reference:
       `true_facet = await laserhead.remap(facet)`.
    2. Calculates revolution count `reps` to fill the FPGA hardware FIFO across the
       full camera exposure duration (`exposure_ms`) with `steps_line=0`.
    3. Flushes any stale DMA frame buffer (`camera.discard_frame()`) on ESP32 so
       the returned frame integrates while the FPGA is actively firing `true_facet`.
    4. Stops the scanline stream (`write_line([])`) and waits for the FIFO to drain.
    """
    true_facet = await laserhead.remap(facet)

    # Set CPython mock hooks for deterministic testing
    Camera.mock_laser_pattern = line
    Camera.mock_active_facet = int(facet)
    Camera.mock_scan_correction_um = float(scan_corr_um)
    Camera.mock_orth_correction_um = float(orth_corr_um)

    if constants.ESP32:
        rpm = float(laserhead.cfg.laser_timing["rpm"])
        rev_time_ms = 60000.0 / max(600.0, rpm)
        exposure_ms = float(CONFIG["calibration"]["camera_exposure_ms"])
        # Pre-load enough revolutions into the 148-line FPGA FIFO to cover exposure_ms
        reps = max(10, min(32, int((exposure_ms * 2.0) / rev_time_ms) + 6))

        await laserhead.write_line(
            line, steps_line=0, repetitions=reps, facet=true_facet
        )
        camera.discard_frame()
        frame = camera.capture_grayscale_array()
        await laserhead.write_line([])
        await laserhead.wait_fifo_empty()
    else:
        await laserhead.write_line(line, steps_line=0, repetitions=3, facet=true_facet)
        frame = camera.capture_grayscale_array()

    return frame


async def run_scan_visibility(
    laserhead,
    camera,
    facet: int = 0,
    step: int = 20,
    progress_callback=None,
):
    """Scan full scanline to determine which pixels fall within sensor FOV.

    1. Enters camera.analysis_mode(SVGA 800x600, GRAYSCALE).
    2. Takes 1 Dark Frame (laser OFF) for background subtraction.
    3. Iterates over scanline_length with step size, projecting 1 pixel on facet.
    4. Uses get_dots_ulab (and clamped uint8 peak check) to determine visibility.
    5. Saves [lower_pixel, upper_pixel] to CONFIG["calibration"]["visible_pixels"].
    6. Camera is automatically restored to UXGA JPEG upon exit.
    """
    scan_len = int(laserhead.cfg.laser_timing["scanline_length"])
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
                laser0=False, laser1=True, polygon=True, synchronize=True
            )

        with camera.analysis_mode(
            frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
        ):
            # 1. Dark Frame Capture (laser OFF)
            Camera.mock_laser_pattern = None
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=True)
            await asyncio.sleep(0.05)
            if constants.ESP32:
                camera.discard_frame()
            dark_frame = camera.capture_grayscale_array()

            if constants.ESP32:
                await laserhead.enable_comp(
                    laser0=False, laser1=True, polygon=True, synchronize=True
                )

            # 2. Iterate and detect signal
            for idx_num, px in enumerate(pixels_to_scan):
                line = [0] * scan_len
                line[px] = 1

                frame = await project_and_capture_facet(
                    laserhead, camera, line, facet=facet
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
        Camera.mock_laser_pattern = None
        Camera.mock_scan_correction_um = 0.0
        Camera.mock_orth_correction_um = 0.0

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


async def run_facet_calibration(
    laserhead,
    camera,
    iterations: int = 2,
    num_dots: int = 6,
    progress_callback=None,
):
    """Run multi-facet dot calibration and persist facet offsets to config."""
    scan_len = int(laserhead.cfg.laser_timing["scanline_length"])
    num_facets = int(laserhead.cfg.laser_timing["facets"])
    bounds = CONFIG["calibration"]["visible_pixels"]
    base_line = make_dot_pattern(scan_len, bounds, num_dots=num_dots)

    all_iter_results = []
    total_captures = max(1, iterations * num_facets)
    done_captures = 0

    try:
        if constants.ESP32:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=True, polygon=True, synchronize=True
            )

        with camera.analysis_mode(
            frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
        ):
            # Capture dark frame
            Camera.mock_laser_pattern = None
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=True)
            await asyncio.sleep(0.05)
            if constants.ESP32:
                camera.discard_frame()
            dark_frame = camera.capture_grayscale_array()

            if constants.ESP32:
                await laserhead.enable_comp(
                    laser0=False, laser1=True, polygon=True, synchronize=True
                )

            for _it in range(iterations):
                facet_frames = []
                for facet in range(num_facets):
                    frame = await project_and_capture_facet(
                        laserhead,
                        camera,
                        base_line,
                        facet=facet,
                        scan_corr_um=0.0,
                        orth_corr_um=0.0,
                    )
                    facet_frames.append(frame)
                    done_captures += 1
                    if progress_callback:
                        pct = int((done_captures / total_captures) * 100)
                        await progress_callback(pct, facet)

                res = calibration_ulab(
                    facet_frames,
                    dark_frame=dark_frame,
                    pixelsize=SVGA_PIXEL_SIZE_UM,
                    compute_rotation=True,
                )
                all_iter_results.append(res)
                del facet_frames
                gc.collect()

    finally:
        Camera.mock_laser_pattern = None
        Camera.mock_scan_correction_um = 0.0
        Camera.mock_orth_correction_um = 0.0

    # Map photodiode facet periods if available
    shift = await laserhead.remap(0)
    raw_means = getattr(laserhead, "cur_facet_means", [5.0] * num_facets)
    if len(raw_means) == num_facets:
        period_means = raw_means[shift:] + raw_means[:shift]
    else:
        period_means = [5.0] * num_facets

    facets_cfg = {}
    for facet in range(num_facets):
        f_key = str(facet)
        scan_vals = [r[f_key]["Scan_shift_um"] for r in all_iter_results]
        orth_vals = [r[f_key]["Orth_shift_um"] for r in all_iter_results]
        spot_vals = [r[f_key]["mean_spot_size_um"] for r in all_iter_results]
        ecc_vals = [r[f_key]["mean_eccentricity"] for r in all_iter_results]
        rot_vals = [r[f_key]["rotation_angle_deg"] for r in all_iter_results]

        scan_mean = float(np.mean(np.array(scan_vals, dtype=NP_FLOAT)))
        orth_mean = float(np.mean(np.array(orth_vals, dtype=NP_FLOAT)))
        scan_std = (
            float(np.std(np.array(scan_vals, dtype=NP_FLOAT)))
            if len(scan_vals) > 1
            else 0.0
        )
        orth_std = (
            float(np.std(np.array(orth_vals, dtype=NP_FLOAT)))
            if len(orth_vals) > 1
            else 0.0
        )
        spot_mean = sum(spot_vals) / len(spot_vals)
        ecc_mean = sum(ecc_vals) / len(ecc_vals)
        rot_mean = sum(rot_vals) / len(rot_vals)

        facets_cfg[f_key] = {
            "scan": round(scan_mean / 1000.0, 6),
            "orth": round(orth_mean / 1000.0, 6),
            "scan_mean_um": round(scan_mean, 2),
            "scan_std_um": round(scan_std, 2),
            "orth_mean_um": round(orth_mean, 2),
            "orth_std_um": round(orth_std, 2),
            "spot_size_um": round(spot_mean, 2),
            "eccentricity": round(ecc_mean, 3),
            "rotation_deg": round(rot_mean, 4),
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
    """Execute Verified Calibration: compare Uncorrected vs Corrected errors.

    1. Captures 4 facets Uncorrected (correction=False) and computes errors Before.
    2. Updates offsets (if calibrate_first=True) and applies 2D facet corrections.
    3. Captures 4 facets Corrected (correction=True) and computes errors After.
    4. Verifies tolerance: max_err_after <= 30 um and max_err_after < max_err_before.
    """
    scan_len = int(laserhead.cfg.laser_timing["scanline_length"])
    num_facets = int(laserhead.cfg.laser_timing["facets"])
    bounds = CONFIG["calibration"]["visible_pixels"]
    base_line = make_dot_pattern(scan_len, bounds, num_dots=num_dots)

    try:
        if constants.ESP32:
            await laserhead.synchronize(True)
            await laserhead.enable_comp(
                laser0=False, laser1=True, polygon=True, synchronize=True
            )

        with camera.analysis_mode(
            frame_size=FrameSize.SVGA, pixel_format=PixelFormat.GRAYSCALE
        ):
            # 1. Dark Frame
            Camera.mock_laser_pattern = None
            await laserhead.enable_comp(laser0=False, laser1=False, polygon=True)
            await asyncio.sleep(0.05)
            if constants.ESP32:
                camera.discard_frame()
            dark_frame = camera.capture_grayscale_array()

            if constants.ESP32:
                await laserhead.enable_comp(
                    laser0=False, laser1=True, polygon=True, synchronize=True
                )

            # 2. Uncorrected pass (Before)
            uncorr_frames = []
            for facet in range(num_facets):
                frame = await project_and_capture_facet(
                    laserhead,
                    camera,
                    base_line,
                    facet=facet,
                    scan_corr_um=0.0,
                    orth_corr_um=0.0,
                )
                uncorr_frames.append(frame)
                if progress_callback:
                    pct = int(((facet + 1) / (num_facets * 2)) * 100)
                    await progress_callback(pct, facet)

            before_res = calibration_ulab(
                uncorr_frames,
                dark_frame=dark_frame,
                pixelsize=SVGA_PIXEL_SIZE_UM,
                compute_rotation=True,
            )
            del uncorr_frames
            gc.collect()

            # Build or load active facet corrections
            shift = await laserhead.remap(0)
            raw_means = getattr(laserhead, "cur_facet_means", [5.0] * num_facets)
            period_means = (
                raw_means[shift:] + raw_means[:shift]
                if len(raw_means) == num_facets
                else [5.0] * num_facets
            )

            facets_cfg = CONFIG["calibration"]["facets"]
            if calibrate_first or not facets_cfg:
                facets_cfg = {}
                for facet in range(num_facets):
                    f_key = str(facet)
                    s_um = float(before_res[f_key]["Scan_shift_um"])
                    o_um = float(before_res[f_key]["Orth_shift_um"])
                    facets_cfg[f_key] = {
                        "scan": round(s_um / 1000.0, 6),
                        "orth": round(o_um / 1000.0, 6),
                        "scan_mean_um": round(s_um, 2),
                        "scan_std_um": 0.0,
                        "orth_mean_um": round(o_um, 2),
                        "orth_std_um": 0.0,
                        "spot_size_um": before_res[f_key]["mean_spot_size_um"],
                        "eccentricity": before_res[f_key]["mean_eccentricity"],
                        "rotation_deg": before_res[f_key]["rotation_angle_deg"],
                        "period_ms": round(float(period_means[facet]), 4),
                    }

            # 3. Corrected pass (After)
            corr_frames = []
            orth_virtual_offsets = []
            for facet in range(num_facets):
                f_key = str(facet)
                s_corr_um = float(facets_cfg[f_key]["scan_mean_um"])
                o_corr_um = float(facets_cfg[f_key]["orth_mean_um"])
                orth_virtual_offsets.append(o_corr_um)

                if constants.ESP32:
                    lanewidth_mm = float(
                        laserhead.cfg.get_optical_params()["lanewidth"]
                    )
                    corr_line = shift_pattern_along_scan(
                        base_line, s_corr_um, scan_len, lanewidth_mm
                    )
                else:
                    corr_line = base_line

                frame = await project_and_capture_facet(
                    laserhead,
                    camera,
                    corr_line,
                    facet=facet,
                    scan_corr_um=s_corr_um,
                    orth_corr_um=o_corr_um,
                )
                corr_frames.append(frame)
                if progress_callback:
                    pct = int(((num_facets + facet + 1) / (num_facets * 2)) * 100)
                    await progress_callback(pct, facet)

            after_res = calibration_ulab(
                corr_frames,
                dark_frame=dark_frame,
                pixelsize=SVGA_PIXEL_SIZE_UM,
                compute_rotation=True,
                orth_virtual_offsets_um=orth_virtual_offsets
                if constants.ESP32
                else None,
            )
            del corr_frames
            gc.collect()

    finally:
        Camera.mock_laser_pattern = None
        Camera.mock_scan_correction_um = 0.0
        Camera.mock_orth_correction_um = 0.0

    # Compute summary metrics and build per-facet comparison table
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
    passed = bool(within_tolerance and improved)

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
        CONFIG["calibration"]["apply_correction"] = bool(apply_correction)

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


async def _bounds_hardware_feeder(laserhead, line: list[int], facet: int | None = None):
    """ESP32 background task feeding FPGA FIFO while Preview Dots is active."""
    try:
        await laserhead.synchronize(True)
        await laserhead.enable_comp(laser0=False, laser1=True, polygon=True, synchronize=True)
        true_facet = (await laserhead.remap(facet)) if facet is not None else None
        while _bounds_active:
            await laserhead.write_line(
                line, steps_line=0, repetitions=12, facet=true_facet
            )
            await asyncio.sleep(0.15)
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Bounds feeder error: {exc}")
    finally:
        try:
            await laserhead.write_line([])
            await laserhead.wait_fifo_empty()
        except Exception:  # noqa: BLE001, S110
            pass


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
        _bounds_active = bool(enabled)

    if facet != "keep":
        if facet is None or facet == "all" or facet == -1 or facet == "-1":
            _preview_facet = None
        else:
            _preview_facet = int(facet)

    bounds = CONFIG["calibration"]["visible_pixels"]
    scan_len = int(laserhead.cfg.laser_timing["scanline_length"])

    if _bounds_active:
        line = make_dot_pattern(scan_len, bounds, num_dots=num_dots)
        Camera.mock_laser_pattern = line
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
                    _bounds_hardware_feeder(laserhead, line, facet=_preview_facet)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Could not start preview feeder task: {exc}")
    else:
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

    bounds = CONFIG["calibration"]["visible_pixels"]
    scan_len = int(laserhead.cfg.laser_timing["scanline_length"])
    line = make_dot_pattern(scan_len, bounds, num_dots=6)
    Camera.mock_laser_pattern = line
    Camera.mock_preview_facet = _preview_facet

    if constants.ESP32:
        await laserhead.synchronize(True)
        await laserhead.enable_comp(laser0=False, laser1=True, polygon=True, synchronize=True)
        rpm = float(laserhead.cfg.laser_timing["rpm"])
        rev_time_ms = 60000.0 / max(600.0, rpm)
        exposure_ms = float(CONFIG["calibration"]["camera_exposure_ms"])
        reps = max(10, min(32, int((exposure_ms * 2.0) / rev_time_ms) + 6))

        if _preview_facet is not None:
            f_line = line
            facets_cfg = CONFIG["calibration"]["facets"]
            apply_corr = bool(CONFIG["calibration"]["apply_correction"])
            if apply_corr and str(_preview_facet) in facets_cfg:
                s_corr_um = float(facets_cfg[str(_preview_facet)]["scan_mean_um"])
                lanewidth_mm = float(laserhead.cfg.get_optical_params()["lanewidth"])
                f_line = shift_pattern_along_scan(
                    line, s_corr_um, scan_len, lanewidth_mm
                )
            true_facet = await laserhead.remap(_preview_facet)
            await laserhead.write_line(
                f_line, steps_line=0, repetitions=reps, facet=true_facet
            )
        else:
            await laserhead.write_line(
                line, steps_line=0, repetitions=reps * 4, facet=None
            )

        camera.discard_frame()
        jpeg_data = camera.capture()
        await laserhead.write_line([])
        await laserhead.wait_fifo_empty()
        return jpeg_data

    return camera.capture()
