import asyncio
import binascii
import hashlib
import logging
import os
import re
import sys

import machine
from microdot import Microdot, Request, Response, redirect, send_file
from microdot.session import Session, with_session
from microdot.sse import with_sse
from microdot.utemplate import Template

from . import bootlib, constants
from .calibration import (
    CalibrationCancelled,
    capture_jpeg_with_preview,
    finish_calibration_status,
    get_calibration_status,
    get_preview_facet,
    is_bounds_active,
    is_calibration_running,
    request_cancel_calibration,
    run_facet_calibration,
    run_pattern_error,
    run_scan_visibility,
    set_bounds_preview,
    start_calibration_status,
    update_custom_facets,
)
from .camera import camera
from .constants import CONFIG, CONFIG_FILE, NVS_FILE, update_config
from .laserhead import laserhead

logger = logging.getLogger(__name__)

# Ensure workspace root is in sys.path when running as entry point (e.g. uv run webapp)
try:
    from pathlib import Path

    workspace_root = str(Path(__file__).resolve().parent.parent.parent)
    if workspace_root not in sys.path:
        sys.path.insert(0, workspace_root)
except ImportError:
    pass

# Template & static paths
if not constants.ESP32:
    static_dir = "src/root/static"
    temp_dir = "src/root/templates"
    Template.initialize(temp_dir)
else:
    static_dir = "static/"


def is_authorized(session):
    """
    Check whether a Microdot session is authorized.

    A session is considered authorized if it contains a ``"password"`` key,
    whose value—after hashing via :func:`pass_to_sha`—matches the configured
    password hash.

    :param dict session: Microdot session object (dict-like).
    :return bool: ``True`` if the session is authorized, otherwise ``False``.
    """
    if not session:
        return False
    pwd = session.get("password")
    return pass_to_sha(pwd) == CONFIG["webserver"]["password"]


def pass_to_sha(password):
    """
    Return the salted SHA-256 hash of a password.

    The password is hashed together with the configured salt using SHA-256.
    If ``password`` is ``None`` or an empty string, an empty string is returned.

    :param str password: Plain-text password to hash.
    :return str: Hex-encoded salted SHA-256 hash.
    """
    if not password:
        return ""
    h = hashlib.sha256()
    h.update(CONFIG["webserver"]["salt"].encode())
    h.update(password.encode())
    return binascii.hexlify(h.digest()).decode()


class DeviceState:
    """Aggregates the total state of the machine for the UI."""

    def __init__(self, laserhead):
        self.laserhead = laserhead
        self._data = {}
        self.update()

    def laserhead_update(self):
        self._data.update(self.laserhead.state)

    @property
    def data(self):
        self.laserhead_update()
        return self._data

    def update(self):
        """
        Updates wifi, file list and laserhead state information.
        """
        self.laserhead_update()
        try:
            files = os.listdir(CONFIG["webserver"]["job_folder"])
        except OSError:
            files = []

        calib_dict = dict(CONFIG["calibration"])
        calib_dict["bounds_active"] = is_bounds_active()
        calib_dict["preview_facet"] = get_preview_facet()
        calib_dict["task"] = get_calibration_status()

        dct = {
            "files": files,
            "wifi": {
                "connected": bootlib.is_connected(),
                "ssid": CONFIG["wifi_login"]["ssid"],
                "password": CONFIG["wifi_login"]["password"],
            },
            "calibration": calib_dict,
        }
        self._data.update(dct)


# Configure Microdot
app = Microdot()
Session(app, secret_key=CONFIG["webserver"]["salt"])
Response.default_content_type = "text/html"
Request.max_content_length = CONFIG["webserver"]["max_content_length"] * 1024 * 1024
devicestate = DeviceState(laserhead)

# --- AUTHENTICATION MIDDLEWARE ---


@app.before_request
@with_session
async def authenticate(request, session):
    """
    Global authentication check before processing any request.
    """
    # 1. Sta toegang toe tot de login pagina en statische bestanden
    if request.path == "/" or request.path.startswith("/static/"):
        return

    # 2. Check autorisatie
    if not is_authorized(session):
        # Voor AJAX/Fetch/SSE calls sturen we een 401 (Unauthorized)
        # Voor gewone pagina navigatie (zoals /logout) redirecten we naar de login
        api_paths = (
            "/api/",
            "/control/",
            "/print/",
            "/state",
            "/gotopoint",
            "/home",
            "/setworkspacezero",
            "/upload",
            "/deletefile",
            "/reset",
            "/clearerror",
        )
        if request.path.startswith(api_paths):
            return {"error": "Unauthorized"}, 401

        return redirect("/")


# --- ROUTES ---


@app.route("/", methods=["GET", "POST"])
@with_session
async def index(req, session):
    if req.method == "POST":
        session["password"] = req.form.get("password")
        if is_authorized(session):
            session.save()
        return redirect("/")

    if is_authorized(session):
        logger.info("User logged in")
        devicestate.update()
        return Template("home.html").generate_async(state=devicestate.data)
    else:
        login_fail = "password" in session
        return Template("login.html").generate_async(login_fail=login_fail)


SAFE_FILENAME_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]")


@app.post("/upload")
@with_session
async def upload(request, session):
    folder = CONFIG["webserver"]["job_folder"]

    # We check if we can stat the folder; if not, we create it.
    try:
        os.stat(folder)
    except OSError:
        try:
            os.mkdir(folder)
            logger.info(f"Created job folder: {folder}")
        except Exception as e:  # noqa: BLE001
            # If we can't create the folder, we really can't proceed
            logger.error(f"Failed to create folder: {e}")
            return {"error": "Cannot create upload folder"}, 500

    # Header Parsing: Safely get Content-Length
    try:
        content_len = int(request.headers.get("Content-Length", 0))
    except ValueError:
        return {"error": "Invalid Content-Length"}, 400

    if content_len == 0:
        return {"error": "Empty file"}, 400

    # Note: iterating os.stat is still slow, but unavoidable without a cached counter.
    try:
        files = os.listdir(folder)
        current_usage_mb = sum(os.stat(f"{folder}/{f}")[6] for f in files) / (
            1024 * 1024
        )

        # Check if adding this file exceeds the limit
        if (current_usage_mb + (content_len / 1024 / 1024)) > CONFIG["webserver"][
            "max_content_length"
        ]:
            return {"error": "Storage quota exceeded"}, 413
    except OSError:
        # Handle case where folder doesn't exist
        return {"error": "Upload folder missing"}, 500

    # Try to extract filename, fallback to a default if parsing fails
    disposition = request.headers.get("Content-Disposition", "")
    match = re.search(r'filename="?([^";]+)"?', disposition)
    if match:
        filename = match.group(1)
        # Strip any path info (keep only basename)
        filename = filename.split("/")[-1].split("\\")[-1]
        # Remove unsafe characters
        filename = SAFE_FILENAME_PATTERN.sub("_", filename)
    else:
        return {"error": "Missing filename in Content-Disposition"}, 400

    filepath = laserhead.get_job_path(filename)

    try:
        with open(filepath, "wb") as f:  # noqa: ASYNC230, fine in micropython
            bytes_remaining = content_len
            chunk_size = 4096

            while bytes_remaining > 0:
                chunk = await request.stream.read(min(bytes_remaining, chunk_size))

                if not chunk:
                    raise OSError("Incomplete upload / Connection closed")

                f.write(chunk)
                bytes_remaining -= len(chunk)

        logger.info(f"Upload complete: {filename} ({content_len} bytes)")

    except Exception as e:  # noqa: BLE001, capture everything just to be safe
        logger.error(f"Upload failed: {e}")
        # Clean up: delete the partial file so it doesn't waste space
        try:
            os.remove(filepath)
        except Exception as remove_err:  # noqa: BLE001, capture everything just to be safe
            logger.error(f"Removal failed: {remove_err}")

        return {"error": "Upload failed", "details": str(e)}, 500

    devicestate.update()
    return {"success": "upload succeeded"}, 200


@app.post("/deletefile")
@with_session
async def delete_file(request, session):
    try:
        jsondata = request.json
        if not jsondata or "file" not in jsondata:
            return {"error": "Missing 'file' parameter"}, 400

        raw_filename = jsondata["file"]

        # This turns "bad/path/../file.gcode" into "bad_path_.._file.gcode"
        # forcing it to stay in the job folder.
        filename = SAFE_FILENAME_PATTERN.sub("_", raw_filename)

        # Extra safety: ensure we strip any remaining path separators just in case
        filename = filename.split("/")[-1].split("\\")[-1]

        if not filename:
            return {"error": "Invalid filename"}, 400

        filepath = laserhead.get_job_path(filename)

        # We use os.stat to check existence first, or just try remove
        try:
            os.remove(filepath)
            logger.info(f"Deleted file: {filename}")
        except OSError:
            # Error 2 usually means No Such File
            return {"error": "File not found"}, 404

        # Updates the file list for other connected clients)
        devicestate.update()

        return {"success": "File deleted"}, 200

    except Exception as e:  # noqa: BLE001, capture everything just to be safe
        logger.error(f"Delete failed: {e}")
        return {"error": "Server error processing delete"}, 500


@app.get("/logout")
@with_session
async def logout(req, session):
    session.delete()
    return redirect("/")


@app.post("/reset")
@with_session
async def reset(req, session):
    logger.debug("Scheduling reset...")

    async def delayed_reset():
        await asyncio.sleep(1)

        logger.info("Rebooting ESP32S3")
        machine.reset()

    asyncio.create_task(delayed_reset())

    return {"status": "success", "message": "Rebooting in 1s..."}


@app.post("/gotopoint")
@with_session
async def api_gotopoint(request, session):
    data = request.json

    position = [float(x) for x in data.get("position", [0, 0, 0])]
    absolute = data["absolute"]
    workspace = data["workspace"]
    state = laserhead.enable_steppers
    laserhead.enable_steppers = True
    try:
        await laserhead.gotopoint(
            position=position, absolute=absolute, workspace=workspace
        )
    except ValueError as ve:
        if state is False:
            laserhead.enable_steppers = False
        await laserhead.set_error(str(ve))
        return {"error": str(ve)}, 400

    if state is False:
        await laserhead.wait_fifo_empty()
        laserhead.enable_steppers = False
    return devicestate.data


@app.post("/control/abort")
@with_session
async def api_abort(request, session):
    request_cancel_calibration()
    await laserhead.emergency_stop()
    return devicestate.data


@app.post("/control/reset")
@with_session
async def api_reset(request, session):
    await laserhead.reset_estop()
    return devicestate.data


@app.post("/setworkspacezero")
@with_session
async def setworkspacezero(request, session):
    data = request.json
    axes = data["axes"]

    await laserhead.set_workspace_zero(axes=axes)
    return devicestate.data


@app.post("/control/spindle")
@with_session
async def control_spindle(request, session):
    data = request.json or {}
    value = data["value"]

    await laserhead.set_spindle(value)
    return devicestate.data


@app.post("/control/fan")
@with_session
async def control_fan(request, session):
    data = request.json
    value = data["value"]

    await laserhead.set_fan(value)
    return devicestate.data


@app.post("/home")
@with_session
async def home(request, session):
    data = request.json
    axes = [int(x) for x in data.get("axes", [0, 0, 0])]
    state = laserhead.enable_steppers
    laserhead.enable_steppers = True
    try:
        await laserhead.home_axes(axes)
    except ValueError as ve:
        if state is False:
            laserhead.enable_steppers = False
        await laserhead.set_error(str(ve))
        return {"error": str(ve)}, 400
    if state is False:
        await laserhead.wait_fifo_empty()
        laserhead.enable_steppers = False
    return devicestate.data


@app.post("/control/laser")
@with_session
async def toggle_laser(request, session):
    # Safety check: Don't toggle hardware while printing
    if laserhead.state["printing"]:
        return {"error": "Cannot toggle laser while printing"}, 409
    await laserhead.toggle_laser()
    # Return the new state immediately so the button updates color instantly
    return devicestate.data


@app.post("/control/prism")
@with_session
async def toggle_prism(request, session):
    if laserhead.state["printing"]:
        return {"error": "Cannot toggle prism while printing"}, 409
    await laserhead.toggle_prism()
    return devicestate.data


@app.post("/control/diodetest")
@with_session
async def diode_test(request, session):
    if laserhead.state["printing"]:
        return {"error": "Busy printing"}, 409

    async def run_test_background():
        await laserhead.test_diode()

    asyncio.create_task(run_test_background())
    return {"status": "started"}


@app.post("/control/save_facet_means")
@with_session
async def save_facet_means(request, session):
    """Saves the last measured diode test facet means as the new baseline reference in config.json."""
    if laserhead.state["printing"]:
        return {"error": "Busy printing"}, 409

    diodetest = laserhead.state["components"].get("diodetest")
    if not diodetest or not isinstance(diodetest, dict) or "facets" not in diodetest:
        return {"error": "No valid diode test data available"}, 400

    facets = diodetest["facets"]
    # Safely parse integer or string keys from JSON/Dict
    facet_dict = {int(k): v for k, v in facets.items()}
    sorted_ids = sorted(facet_dict.keys())
    new_means = [float(facet_dict[i]["mean_ms"]) for i in sorted_ids]

    if not new_means:
        return {"error": "No facet timing data to save"}, 400

    if "laserhead" not in CONFIG:
        CONFIG["laserhead"] = {}
    CONFIG["laserhead"]["facetmeans"] = new_means
    update_config()

    # Update current diodetest report ref values so UI reflects the new reference instantly
    for facet_data in facets.values():
        facet_data["ref_mean_ms"] = round(float(facet_data["mean_ms"]), 4)
        facet_data["delta_mean_ms"] = 0.0000

    await laserhead.notify_listeners()
    logger.info(f"Saved new calibration reference facet means: {new_means}")
    return laserhead.state


@app.post("/print/control")
@with_session
async def print_control(request, session):
    # Let it raise a KeyError if request.json is missing or None
    data = request.json
    action = data["action"]

    if action == "start":
        if laserhead.state["printing"]:
            return {"error": "Already printing"}, 409

        # Reset any previous error state
        laserhead.state["error_message"] = None

        # Let it raise a KeyError if "file" is missing
        filename = data["file"].replace("/", "_")

        # Determine job type based on file extension
        is_gcode = filename.lower().endswith((".gcode", ".nc", ".tap"))

        cfg_print = CONFIG["defaultprint"]
        if not is_gcode:
            cfg_print["laserpower"] = int(data["laserpower"])
            cfg_print["exposureperline"] = int(data["exposureperline"])
            cfg_print["singlefacet"] = bool(data["singlefacet"])
            cfg_print["lanewidth_correction"] = float(data["lanewidth_correction"])

        # Shared settings
        cfg_print["workspace_origin"] = data["workspace_origin"]
        cfg_print["home_before_print"] = bool(data["home_before_print"])
        cfg_print["use_custom_start"] = bool(data["use_custom_start"])

        update_config()

        # Start background task
        async def run_job():
            try:
                if is_gcode:
                    if cfg_print["home_before_print"]:
                        logger.info("Homing X and Y axes before G-code execution.")
                        await laserhead.home_axes([1, 1, 0])

                    await laserhead.execute_gcode(filename)
                else:
                    await laserhead.print_loop(filename)
            except Exception as e:  # noqa: BLE001, just for certainty
                logger.error(f"Print job failed with exception: {e}")
                await laserhead.set_error(str(e))
                await laserhead.notify_listeners()
            finally:
                devicestate.laserhead_update()

        asyncio.create_task(run_job())

    elif action == "stop":
        await laserhead.stop_print()
        devicestate.laserhead_update()
    elif action == "pause":
        await laserhead.pause_print()
        devicestate.laserhead_update()

    return devicestate.data


@app.post("/clearerror")
@with_session
async def clear_error_route(request, session):
    logger.info("Clearing error state.")
    await laserhead.clear_error()
    devicestate.laserhead_update()
    return devicestate.data


@app.route("/state")
@with_sse
@with_session
async def state(request, session, sse):
    # Send initial state immediately upon connection
    await sse.send(devicestate.data)

    while True:
        try:
            # WAIT here forever until update_event.set() is called
            # This uses 0 CPU.
            await laserhead.statechange.wait()
            devicestate.laserhead_update()
            # We woke up! Send the data.
            await sse.send(devicestate.data, event="message")
            # YIELD to ensure the data actually goes out before we wait again
            await asyncio.sleep(0)
        except Exception as e:  # noqa: BLE001
            logger.error(f"State update failed: {e}")
            break


@app.get("/api/settings")
@with_session
async def get_settings(request, session):
    """
    Exposes the active machine, network, and tool settings to the frontend.
    """
    return {
        "wifi_login": CONFIG["wifi_login"],
        "motors": CONFIG["motors"],
        "tools": CONFIG["tools"],
        "defaultprint": CONFIG["defaultprint"],
        "calibration": CONFIG["calibration"],
    }


@app.post("/api/settings")
@with_session
async def save_settings(request, session):
    """
    Receives modified settings from the UI and commits them to config.json.
    """
    data = request.json

    for key in ("wifi_login", "motors", "tools", "defaultprint", "calibration"):
        if key in data:
            CONFIG[key] = data[key]

    update_config()
    laserhead.apply_motor_settings()
    devicestate.update()

    logger.info("Configuration updated successfully.")
    return {"status": "success", "message": "Settings saved"}


@app.get("/api/system/update/check")
@with_session
async def api_check_update(request, session):
    """
    Checks the GitHub API for a newer release tag.
    """
    logger.info("Checking GitHub for firmware updates...")

    current_version = constants.get_firmware_version()

    # get_firmware_dct returns {} if no new update is found
    release_dct = bootlib.get_firmware_dct(require_new=True)

    if release_dct:
        latest_version = release_dct.get("tag_name", "unknown")
        return {
            "update_available": True,
            "current_version": current_version,
            "latest_version": latest_version,
        }
    else:
        return {
            "update_available": False,
            "current_version": current_version,
            "latest_version": current_version,
        }


@app.post("/api/system/update/apply")
@with_session
async def api_apply_update(request, session):
    """
    Triggers the OTA update process in the background.
    """
    logger.warning("OTA Firmware update triggered via web interface!")

    # Check if the UI sent {"force": true}
    payload = request.json if request.json else {}
    force_update = payload.get("force", False)

    async def background_update():
        # Give the web server 1 second to return the JSON response to the browser
        await asyncio.sleep(1)

        # (Optional) If you have a global state dict for SSE, you could flag it here:
        # machine_state["system_status"] = "Updating Firmware..."

        success = await bootlib.update_firmware(force=force_update)

        if not success:
            logger.error("Background OTA update failed.")
            # machine_state["system_status"] = "Update Failed"

    # Dispatch the update task to the asyncio event loop
    asyncio.create_task(background_update())

    return {
        "status": "success",
        "message": "Firmware download started. The system will flash and reboot automatically.",
    }


@app.post("/api/settings/reset")
@with_session
async def api_factory_reset(request, session):
    """
    Wipes the active configuration files and triggers a hard reboot.
    On restart, the system will redeploy factory defaults.
    """
    logger.warning("FACTORY RESET TARGETED! Wiping configuration...")

    # Delete active mutable configuration JSONs
    for file_path in (CONFIG_FILE, NVS_FILE):
        try:
            os.remove(file_path)
            logger.info(f"Deleted {file_path}")
        except OSError:
            pass  # File didn't exist

    # Trigger asynchronous reboot sequence
    async def delayed_reset():
        await asyncio.sleep(1)

        machine.reset()  # On ESP32 reboots hardware. On PC, triggers os._exit(0).

    asyncio.create_task(delayed_reset())
    return {"status": "success", "message": "Factory reset initiated, rebooting..."}


@app.get("/api/camera/snapshot")
@with_session
async def camera_snapshot(request, session):
    """
    Return a single JPEG snapshot from the camera.
    Uses OV2640 hardware JPEG on ESP32 or simulated mock on PC.
    """
    if is_calibration_running():
        return Response(
            "Camera busy: calibration in progress",
            status_code=409,
            headers={"Content-Type": "text/plain"},
        )

    jpeg_data = await capture_jpeg_with_preview(laserhead, camera)
    if not jpeg_data:
        err_msg = camera.last_error or "Camera hardware capture failed"
        return Response(
            err_msg, status_code=500, headers={"Content-Type": "text/plain"}
        )

    return Response(
        body=jpeg_data,
        headers={
            "Content-Type": "image/jpeg",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.post("/api/camera/settings")
@with_session
async def api_camera_settings(request, session):
    """Update camera exposure and optical settings."""
    data = request.json
    exp_ms = max(1, int(data["exposure_ms"]))
    CONFIG["calibration"]["camera_exposure_ms"] = exp_ms
    update_config()
    devicestate.update()

    return devicestate.data


@app.post("/api/calibration/save_camera_pos")
@with_session
async def api_save_camera_pos(request, session):
    """Save current (or specified) machine position as camera calibration target."""
    data = request.json
    pos = data["position"]
    pos = [round(float(x), 2) for x in pos[:3]]

    CONFIG["calibration"]["camera_position"] = pos
    update_config()
    devicestate.update()
    return devicestate.data


@app.post("/api/calibration/move_to_camera")
@with_session
async def api_move_to_camera(request, session):
    """Move laser head directly to the saved camera calibration position."""
    target = CONFIG["calibration"]["camera_position"]

    state = laserhead.enable_steppers
    laserhead.enable_steppers = True
    try:
        await laserhead.gotopoint(position=target, absolute=True, workspace=False)
    except ValueError as ve:
        if state is False:
            laserhead.enable_steppers = False
        await laserhead.set_error(str(ve))
        return {"error": str(ve)}, 400

    if state is False:
        await laserhead.wait_fifo_empty()
        laserhead.enable_steppers = False
    return devicestate.data


_calib_bg_task = None


async def _run_calibration_managed(task_name: str, coro_factory):
    """Run calibration task, updating calibration status & SSE listeners on every step."""

    async def _progress_cb(_pct, _detail):
        devicestate.update()
        await laserhead.notify_listeners()

    start_calibration_status(task_name, f"Starting {task_name}...")
    devicestate.update()
    await laserhead.notify_listeners()
    try:
        result = await coro_factory(_progress_cb)
        timing = result.get("timing") or {}
        elapsed_s = float(timing.get("total_ms", 0)) / 1000.0
        captures = int(timing.get("captures", 0))
        if task_name == "scan_visibility":
            bounds = result.get("visible_pixels")
            msg = (
                f"Visible px {bounds[0]}..{bounds[1]} ({result.get('percentage', 0)}%, {captures} frames in {elapsed_s:.1f}s)"
                if bounds
                else f"No signal in FOV ({captures} frames in {elapsed_s:.1f}s)"
            )
        elif task_name == "pattern_error":
            verdict = "PASS" if result.get("passed") else "FAIL"
            msg = f"2D Verified ({verdict}: {result.get('max_error_after_um')} um, {captures} frames in {elapsed_s:.1f}s)"
        else:
            msg = f"Calibration complete ({captures} frames in {elapsed_s:.1f}s)"
        finish_calibration_status("done", msg)
        devicestate.update()
        await laserhead.notify_listeners()
        return result
    except CalibrationCancelled as exc:
        finish_calibration_status("cancelled", str(exc))
        devicestate.update()
        await laserhead.notify_listeners()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Calibration task {task_name} failed: {exc}")
        finish_calibration_status("error", f"{task_name} failed: {exc}", str(exc))
        devicestate.update()
        await laserhead.notify_listeners()
        raise


def _execute_calibration(task_name: str, coro_factory, background: bool):
    """Guard busy state and run calibration either as background task (202) or inline."""
    global _calib_bg_task
    if laserhead.state["printing"] or is_calibration_running():
        return {"error": "Busy printing or calibrating"}, 409

    if background:

        async def _bg_runner():
            try:
                await _run_calibration_managed(task_name, coro_factory)
            except CalibrationCancelled:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Background calibration {task_name} failed: {exc}")

        start_calibration_status(task_name, f"Starting {task_name}...")
        devicestate.update()
        _calib_bg_task = asyncio.create_task(_bg_runner())
        return (
            {
                "status": "started",
                "task": task_name,
                "calibration_status": get_calibration_status(),
            },
            202,
        )

    return None


@app.get("/api/calibration/status")
@with_session
async def api_calibration_status(request, session):
    """Return live calibration task progress, timing telemetry, and error info."""
    return get_calibration_status()


@app.post("/api/calibration/cancel")
@with_session
async def api_calibration_cancel(request, session):
    """Request cancellation of any active calibration workflow."""
    cancelled = request_cancel_calibration()
    devicestate.update()
    await laserhead.notify_listeners()
    return {
        "status": "cancelling" if cancelled else "idle",
        "calibration_status": get_calibration_status(),
    }


@app.post("/api/calibration/scan_visibility")
@with_session
async def api_scan_visibility(request, session):
    """Run optical scan visibility calibration across scanline length."""
    data = request.json or {}
    facet = int(data["facet"])
    step = max(1, int(data["step"]))
    fast = bool(data.get("fast", True))
    background = bool(data.get("background", False))

    def _run(cb):
        return run_scan_visibility(
            laserhead=laserhead,
            camera=camera,
            facet=facet,
            step=step,
            fast=fast,
            progress_callback=cb,
        )

    bg_resp = _execute_calibration("scan_visibility", _run, background=background)
    if bg_resp is not None:
        await laserhead.notify_listeners()
        return bg_resp

    try:
        return await _run_calibration_managed("scan_visibility", _run)
    except CalibrationCancelled as exc:
        return {"error": str(exc)}, 409
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}, 500


@app.post("/api/calibration/show_bounds")
@with_session
async def api_show_bounds(request, session):
    """Toggle or configure projection of calibration dots (1 facet or all facets)."""
    data = request.json
    enabled = data["enabled"]
    facet = data["facet"]
    if enabled and is_calibration_running():
        return {"error": "Busy calibrating"}, 409
    active = set_bounds_preview(laserhead=laserhead, enabled=enabled, facet=facet)

    devicestate.update()
    await laserhead.notify_listeners()
    return {
        "status": "success",
        "bounds_active": active,
        "preview_facet": get_preview_facet(),
        "visible_pixels": CONFIG["calibration"]["visible_pixels"],
    }


@app.post("/api/calibration/calibrate")
@with_session
async def api_calibrate_facets(request, session):
    """Run multi-facet dot calibration on-board."""
    data = request.json or {}
    iterations = max(1, int(data["iterations"]))
    num_dots = max(2, int(data["num_dots"]))
    background = bool(data.get("background", False))

    def _run(cb):
        return run_facet_calibration(
            laserhead=laserhead,
            camera=camera,
            iterations=iterations,
            num_dots=num_dots,
            progress_callback=cb,
        )

    bg_resp = _execute_calibration("calibrate", _run, background=background)
    if bg_resp is not None:
        await laserhead.notify_listeners()
        return bg_resp

    try:
        return await _run_calibration_managed("calibrate", _run)
    except CalibrationCancelled as exc:
        return {"error": str(exc)}, 409
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}, 500


@app.post("/api/calibration/pattern_error")
@with_session
async def api_pattern_error(request, session):
    """Run Verified Calibration (pattern_error) comparing Before vs After 2D errors."""
    data = request.json or {}
    num_dots = max(2, int(data["num_dots"]))
    calibrate_first = bool(data["calibrate_first"])
    exposures_2d = max(1, int(data.get("exposures_2d", 2)))
    verify_before_2d = bool(data.get("verify_before_2d", False))
    slicer = str(data.get("slicer", "analytical"))
    background = bool(data.get("background", False))

    def _run(cb):
        return run_pattern_error(
            laserhead=laserhead,
            camera=camera,
            num_dots=num_dots,
            calibrate_first=calibrate_first,
            exposures_2d=exposures_2d,
            verify_before_2d=verify_before_2d,
            slicer=slicer,
            progress_callback=cb,
        )

    bg_resp = _execute_calibration("pattern_error", _run, background=background)
    if bg_resp is not None:
        await laserhead.notify_listeners()
        return bg_resp

    try:
        return await _run_calibration_managed("pattern_error", _run)
    except CalibrationCancelled as exc:
        return {"error": str(exc)}, 409
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}, 500


@app.post("/api/calibration/update_facets")
@with_session
async def api_update_facets(request, session):
    """Save custom per-facet (Scan / Orth) calibration offsets and/or toggle apply_correction."""
    data = request.json
    facets_override = data["facets"]
    apply_correction = data["apply_correction"]

    result = update_custom_facets(
        facets_override=facets_override,
        apply_correction=apply_correction,
    )
    devicestate.update()
    await laserhead.notify_listeners()
    return result


@app.route("/static/<path:path>")
async def static(request, path):
    if ".." in path:
        # directory traversal is not allowed
        return "Not found", 404
    cache_age = 86400 if constants.ESP32 else 0
    return send_file(f"{static_dir}/" + path, max_age=cache_age)


def main():
    from control.bootlib import set_log_level

    laserhead.debug = True
    set_log_level(logging.DEBUG)
    logger.info("Started logging")

    python_files = [f for f in os.listdir(temp_dir) if ".py" in f]
    for f in python_files:
        os.remove(temp_dir + "/" + f)

    asyncio.run(app.start_server(port=5000, debug=True))


if __name__ == "__main__":
    main()
