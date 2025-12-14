import asyncio
import binascii
import hashlib
import logging
import os
import re

from microdot import Microdot, Request, Response, redirect, send_file
from microdot.session import Session, with_session
from microdot.sse import with_sse
from microdot.utemplate import Template

import machine

from . import bootlib, constants
from .laserhead import LASERHEAD

logger = logging.getLogger(__name__)

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
    pwd = session.get("password")
    return pass_to_sha(pwd) == constants.CONFIG["webserver"]["password"]


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
    h.update(constants.CONFIG["webserver"]["salt"].encode())
    h.update(password.encode())
    return binascii.hexlify(h.digest()).decode()


class DeviceState:
    """Aggregates the total state of the machine for the UI."""

    def __init__(self, laserhead):
        self.laserhead = laserhead
        self.data = {}
        self.update()

    def laserhead_update(self):
        self.data.update(self.laserhead.state)

    def update(self):
        """
        Updates wifi, file list and laserhead state information.
        """
        self.laserhead_update()
        try:
            files = os.listdir(constants.CONFIG["webserver"]["job_folder"])
        except OSError:
            files = []

        dct = {
            "files": files,
            "wifi": {
                "connected": bootlib.is_connected(),
                "available": bootlib.list_wlans(),  # <--- This is the slow part
                "ssid": constants.CONFIG["wifi_login"]["ssid"],
                "password": constants.CONFIG["wifi_login"]["password"],
            },
        }
        self.data.update(dct)


# Configure Microdot
app = Microdot()
Session(app, secret_key=constants.CONFIG["webserver"]["salt"])
Response.default_content_type = "text/html"
Request.max_content_length = (
    constants.CONFIG["webserver"]["max_content_length"] * 1024 * 1024
)
devicestate = DeviceState(LASERHEAD)


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
    if not is_authorized(session):
        # Fail fast: Return 401 immediately
        return {"unauthorized": "please login"}, 401

    folder = constants.CONFIG["webserver"]["job_folder"]

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
        if (current_usage_mb + (content_len / 1024 / 1024)) > constants.CONFIG[
            "webserver"
        ]["max_content_length"]:
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

    filepath = f"{folder}/{filename}"

    try:
        with open(filepath, "wb") as f:
            bytes_remaining = content_len
            chunk_size = 4096

            while bytes_remaining > 0:
                chunk = await request.stream.read(min(bytes_remaining, chunk_size))

                if not chunk:
                    raise OSError("Incomplete upload / Connection closed")

                f.write(chunk)
                bytes_remaining -= len(chunk)

        logger.info(f"Upload complete: {filename} ({content_len} bytes)")

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        # Clean up: delete the partial file so it doesn't waste space
        try:
            os.remove(filepath)
        except:
            pass
        return {"error": "Upload failed", "details": str(e)}, 500

    devicestate.update()
    return {"success": "upload succeeded"}, 200


@app.post("/deletefile")
@with_session
async def delete_file(request, session):
    if not is_authorized(session):
        return {"unauthorized": "please login"}, 401

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

        folder = constants.CONFIG["webserver"]["job_folder"]
        filepath = f"{folder}/{filename}"

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

    except Exception as e:
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
    if not is_authorized(session):
        return redirect("/")

    logger.debug("Scheduling reset...")

    async def delayed_reset():
        await asyncio.sleep(1)

        logger.info("Rebooting ESP32S3")
        machine.reset()

    asyncio.create_task(delayed_reset())

    return {"status": "success", "message": "Rebooting in 1s..."}


@app.post("/move")
@with_session
async def move(request, session):
    if not is_authorized(session):
        return {"error": "Unauthorized"}, 401

    data = request.json
    steps = float(data.get("steps", 1))
    vector = [int(x) * steps for x in data.get("vector", [0, 0, 0])]
    await LASERHEAD.move(vector)

    return devicestate.data


@app.post("/control/laser")
@with_session
async def toggle_laser(request, session):
    if not is_authorized(session):
        return {"error": "Unauthorized"}, 401

    # Safety check: Don't toggle hardware while printing
    if LASERHEAD.state["printing"]:
        return {"error": "Cannot toggle laser while printing"}, 409
    await LASERHEAD.toggle_laser()
    # Return the new state immediately so the button updates color instantly
    return devicestate.data


@app.post("/control/prism")
@with_session
async def toggle_prism(request, session):
    if not is_authorized(session):
        return {"error": "Unauthorized"}, 401
    if LASERHEAD.state["printing"]:
        return {"error": "Cannot toggle prism while printing"}, 409
    await LASERHEAD.toggle_prism()
    return devicestate.data


@app.post("/control/diodetest")
@with_session
async def diode_test(request, session):
    if not is_authorized(session):
        return {"error": "Unauthorized"}, 401

    if LASERHEAD.state["printing"]:
        return {"error": "Busy printing"}, 409

    # state to "Running" (null)
    devicestate.data["components"]["diodetest"] = None

    async def run_test_background():
        await LASERHEAD.test_diode()

    asyncio.create_task(run_test_background())
    # Result diode test retrieved via SSE
    return devicestate.data


@app.post("/print/control")
@with_session
async def print_control(request, session):
    if not is_authorized(session):
        return {"error": "Unauthorized"}, 401

    data = request.json
    action = data.get("action")

    if action == "start":
        if LASERHEAD.state["printing"]:
            return {"error": "Already printing"}, 409
        # Parse settings
        filename = data["file"].replace("/", "_")
        constants.CONFIG["defaultprint"]["laserpower"] = int(data["laserpower"])
        constants.CONFIG["defaultprint"]["exposureperline"] = int(
            data["exposureperline"]
        )
        constants.CONFIG["defaultprint"]["singlefacet"] = bool(data["singlefacet"])
        constants.update_config()

        # Start background print task
        async def print_task():
            await LASERHEAD.print_loop(filename)
            devicestate.laserhead_update()

        asyncio.create_task(print_task())
    elif action == "stop":
        LASERHEAD.stop_print()
    elif action == "pause":
        LASERHEAD.pause_print()
    return devicestate.data


@app.route("/state")
@with_sse
@with_session
async def state(request, session, sse):
    if not is_authorized(session):
        await sse.send({"notauthorized": 0}, event="message")
        return

    # Send initial state immediately upon connection
    await sse.send(devicestate.data)

    while True:
        try:
            # WAIT here forever until update_event.set() is called
            # This uses 0 CPU.
            await LASERHEAD.statechange.wait()
            devicestate.laserhead_update()
            # We woke up! Send the data.
            await sse.send(devicestate.data, event="message")
            # YIELD to ensure the data actually goes out before we wait again
            await asyncio.sleep(0)
        except Exception:
            break


@app.route("/static/<path:path>")
async def static(request, path):
    if ".." in path:
        # directory traversal is not allowed
        return "Not found", 404
    return send_file(f"{static_dir}/" + path, max_age=86400)  # cache for 1 day


if __name__ == "__main__":
    from control.bootlib import set_log_level

    LASERHEAD.debug = True
    set_log_level(logging.DEBUG)
    logger.info("Started logging")
    python_files = [f for f in os.listdir(temp_dir) if ".py" in f]
    for f in python_files:
        os.remove(temp_dir + "/" + f)
    asyncio.run(app.start_server(port=5000, debug=True))
