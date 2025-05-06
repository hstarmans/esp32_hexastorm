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

if not constants.ESP32:
    static_dir = "src/root/static"
    temp_dir = "src/root/templates"
    Template.initialize(temp_dir)
else:
    static_dir = "static/"


app = Microdot()
Session(app, secret_key=constants.CONFIG["webserver"]["salt"])
Response.default_content_type = "text/html"
Request.max_content_length = (
    constants.CONFIG["webserver"]["max_content_length"] * 1024 * 1024
)
logger = logging.getLogger(__name__)


def is_authorized(session):
    pwd = session.get("password")
    if pass_to_sha(pwd) == constants.CONFIG["webserver"]["password"]:
        return True
    else:
        return False


def pass_to_sha(password):
    """convert pass to sha by adding salt
    returns a string
    """
    if isinstance(password, type(None)):
        return 0
    h = hashlib.sha256()
    h.update(constants.CONFIG["webserver"]["salt"].encode())
    h.update(password.encode())
    return binascii.hexlify(h.digest()).decode()


class Webstate:
    """Defines dictionary which is shared with web front end."""

    def __init__(self):
        self.state = {}
        self.update()

    def partial_update(self):
        self.state.update(LASERHEAD.state)

    def update(self):
        self.partial_update()
        dct = {
            "files": os.listdir(constants.CONFIG["webserver"]["job_folder"]),
            "wifi": {
                "connected": bootlib.is_connected(),
                "available": bootlib.list_wlans(),
                "ssid": constants.CONFIG["wifi_login"]["ssid"],
                "password": constants.CONFIG["wifi_login"]["password"],
            },
        }
        self.state.update(dct)


webstate = Webstate()


@app.route("/", methods=["GET", "POST"])
@with_session
async def index(req, session):
    authorized = is_authorized(session)
    if req.method == "POST":
        session["password"] = req.form.get("password")
        session.save()
        return redirect("/")
    if authorized:
        logger.info("User logged in")
        webstate.update()
        return Template("home.html").generate_async(
            authorized=authorized, state=webstate.state
        )
    else:
        return (
            Template("login.html").generate_async(authorized=authorized),
            401,
        )


@app.post("/upload")
@with_session
async def upload(request, session):
    authorized = is_authorized(session)
    folder = constants.CONFIG["webserver"]["job_folder"]
    if authorized:
        files = os.listdir(folder)
        if (len(files) > 0) & (
            sum(os.stat(folder + "/" + f)[6] for f in files) / (1024 * 1024)
            > constants.CONFIG["webserver"]["max_content_length"]
        ):
            return {"error": "resource not found"}, 413

        # obtain the filename and size from request headers
        filename = (
            request.headers["Content-Disposition"]
            .split("filename=")[1]
            .strip('"')
        )
        size = int(request.headers["Content-Length"])

        # sanitize the filename
        filename = filename.replace("/", "_")

        # write the file to the files directory in 1K chunks
        with open(folder + "/" + filename, "wb") as f:
            while size > 0:
                chunk = await request.stream.read(min(size, 1024))
                f.write(chunk)
                size -= len(chunk)
                logger.info(f"processed {size}")
        res = {"success": "upload succeeded"}, 200
    else:
        res = {"unauthorized": "please login"}, 401
    webstate.update()
    return res


@app.get("/logout")
@with_session
async def logout(req, session):
    session.delete()
    return redirect("/")

@app.get("/reset")
@with_session
async def reset(req, session):
    authorized = is_authorized(session)
    if authorized:
        logger.info("reset machine")
        machine.reset()
    return redirect("/")


@app.route("/command")
@with_websocket
@with_session
async def command(request, session, ws):
    authorized = is_authorized(session)
    if not authorized:
        return redirect("/")
    while authorized:
        data = await ws.receive()
        try:
            jsondata = json.loads(data)
            logger.debug(jsondata)
            command = jsondata["command"]
            if LASERHEAD.state["printing"]:
                if command == "stopprint":
                    LASERHEAD.stop_print()
                elif command == "pauseprint":
                    LASERHEAD.pause_print()
            else:
                if command == "toggleprism":
                    LASERHEAD.toggle_prism()
                elif command == "togglelaser":
                    LASERHEAD.toggle_laser()
                elif command == "diodetest":
                    webstate.state["components"]["diodetest"] = None
                    await ws.send(json.dumps(webstate.state))
                    await LASERHEAD.test_diode()
                elif command == "move":
                    steps = float(jsondata["steps"])
                    vector = [int(x) * steps for x in jsondata["vector"]]
                    LASERHEAD.move(vector)
                elif command == "deletefile":
                    filename = jsondata["file"].replace("/", "_")
                    logger.info(f"Deleting {filename}")
                    os.remove(
                        constants.CONFIG["webserver"]["job_folder"]
                        + "/"
                        + filename
                    )
                    webstate.update()
                elif command == "startwebrepl":
                    bootlib.start_webrepl()
                    request.app.shutdown()
                elif command == "startprint":
                    filename = jsondata["file"].replace("/", "_")
                    constants.CONFIG["defaultprint"]["laserpower"] = int(jsondata["laserpower"])
                    constants.CONFIG["defaultprint"]["exposureperline"] = int(jsondata["exposureperline"])
                    constants.update_config()

                    # actual update is pushed via /state, i.e. SSE not websocket
                    async def task_wrapper():
                        await LASERHEAD.print_loop(filename)
                        webstate.partial_update()

                    # start the print loop
                    asyncio.create_task(task_wrapper())
            webstate.partial_update()
        except Exception as e:
            logger.error(f"Error in command {e}")
        await ws.send(json.dumps(webstate.state))


@app.route("/state")
@with_sse
@with_session
async def state(request, session, sse):
    authorized = is_authorized(session)
    if authorized:
        await sse.send(webstate.state, event="message")
    else:
        await sse.send({"notauthorized": 0}, event="message")


@app.route("/favicon.ico")
async def favicon(request):
    return send_file(f"{static_dir}/favicon.webp", max_age=86400)


@app.route("/static/<path:path>")
async def static(request, path):
    if ".." in path:
        # directory traversal is not allowed
        return "Not found", 404
    return send_file(f"{static_dir}/" + path, max_age=86400)


if __name__ == "__main__":
    logging.basicConfig()
    LASERHEAD.debug = True
    logger.setLevel(logging.DEBUG)
    logger.info("Started logging")
    python_files = [f for f in os.listdir(temp_dir) if ".py" in f]
    for f in python_files:
        os.remove(temp_dir + "/" + f)
    asyncio.run(app.start_server(port=5000, debug=True))
