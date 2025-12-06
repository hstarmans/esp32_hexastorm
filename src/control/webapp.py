import asyncio
import binascii
import hashlib
import json
import logging
import os

from microdot import Microdot, Request, Response, redirect, send_file
from microdot.session import Session, with_session
from microdot.sse import with_sse
from microdot.utemplate import Template
from microdot.websocket import with_websocket

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


# @app.post("/upload")
# @with_session
# async def upload(request, session):
#     authorized = is_authorized(session)
#     folder = constants.CONFIG["webserver"]["job_folder"]
#     if authorized:
#         files = os.listdir(folder)
#         if (len(files) > 0) & (
#             sum(os.stat(folder + "/" + f)[6] for f in files) / (1024 * 1024)
#             > constants.CONFIG["webserver"]["max_content_length"]
#         ):
#             return {"error": "resource not found"}, 413

#         # obtain the filename and size from request headers
#         filename = (
#             request.headers["Content-Disposition"]
#             .split("filename=")[1]
#             .strip('"')
#         )
#         size = int(request.headers["Content-Length"])

#         # sanitize the filename
#         filename = filename.replace("/", "_")

#         # write the file to the files directory in 1K chunks
#         with open(folder + "/" + filename, "wb") as f:
#             while size > 0:
#                 chunk = await request.stream.read(min(size, 1024))
#                 f.write(chunk)
#                 size -= len(chunk)
#                 logger.info(f"processed {size}")
#         res = {"success": "upload succeeded"}, 200
#     else:
#         res = {"unauthorized": "please login"}, 401
#     webstate.update()
#     return res


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
        await LASERHEAD.test_diode()  # Runs for 15s

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
            devicestate.partial_update()

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
