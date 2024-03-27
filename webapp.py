import asyncio
import random
from time import time
import json
import os
import hashlib
import binascii

from microdot import Microdot, Response, redirect, send_file, Request
from microdot.websocket import with_websocket
from microdot.session import Session, with_session
from microdot.utemplate import Template
from microdot.sse import with_sse

import bootlib
import constants


app = Microdot()
Session(app, secret_key=constants.SECRET_KEY)
Response.default_content_type = "text/html"
Request.max_content_length = constants.MAX_CONTENT_LENGTH


def pass_to_sha(password):
    """convert pass to sha by adding salt
    returns a string
    """
    h = hashlib.sha256()
    h.update(constants.SECRET_KEY.encode())
    h.update(password.encode())
    return binascii.hexlify(h.digest()).decode()


def is_authorized(session):
    password = session.get("password")
    if password == constants.PASSWORD:
        return True
    elif password is None:
        return None
    else:
        return False


@app.get("/")
@app.post("/")
@with_session
async def index(req, session):
    authorized = is_authorized(session)
    state = constants.MACHINE_STATE
    if req.method == "POST":
        password = req.form.get("password")
        session["password"] = pass_to_sha(password)
        session.save()
        return redirect("/")
    if authorized:
        state["files"] = os.listdir(constants.UPLOAD_FOLDER)
        state["wifi"]["available"] = bootlib.list_wlans()
        return Template("home.html").render(authorized=authorized, state=state)
    else:
        return Template("login.html").render(authorized=authorized)


@app.post("/upload")
@with_session
async def upload(request, session):
    authorized = is_authorized(session)
    folder = constants.UPLOAD_FOLDER
    if authorized:
        if (
            sum(os.stat(folder + "/" + f)[6] for f in os.listdir(folder))
            / (1024 * 1024)
            > constants.MAX_CONTENT_LENGTH
        ):
            return {"error": "resource not found"}, 413
        # obtain the filename and size from request headers
        filename = (
            request.headers["Content-Disposition"].split("filename=")[1].strip('"')
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
        print("Successfully saved file: " + filename)
    return ""


@app.get("/logout")
@app.post("/logout")
@with_session
async def logout(req, session):
    session.delete()
    return redirect("/")


@app.route("/command")
@with_websocket
@with_session
async def movement(request, session, ws):
    state = constants.MACHINE_STATE
    while True and is_authorized(session):
        data = await ws.receive()
        try:
            jsondata = json.loads(data)
            command = jsondata["command"]
            if state["printing"]:
                if command == "stopprint":
                    constants.STOP_PRINT.set()
                elif command == "pauseprint":
                    constants.PAUSE_PRINT.set()
            else:
                control = state["control"]
                if command == "toggleprism":
                    control["rotating"] = not control["rotating"]
                    print(f"Change rotation state prism to {control['rotating']}")
                elif command == "togglelaser":
                    control["laser"] = not control["laser"]
                    print(f"Laser on is {control['laser']}")
                elif command == "diodetest":
                    control["diodetest"] = None
                    await ws.send(json.dumps(state))
                    await asyncio.sleep(3)
                    control["diodetest"] = True if random.randint(0, 10) > 5 else False
                    print(f"Diode test is {control['diodetest']}")
                elif command == "move":
                    steps = float(jsondata["steps"])
                    vector = [int(x) for x in jsondata["vector"]]
                    print(f"Moving {steps} along {vector}")
                elif command == "deletefile":
                    filename = jsondata["file"].replace("/", "_")
                    os.remove(constants.UPLOAD_FOLDER + "/" + filename)
                elif command == "startprint":
                    filename = jsondata["file"].replace("/", "_")
                    constants.MACHINE_STATE = constants.init_state()
                    # relink state
                    state = constants.MACHINE_STATE
                    state["files"] = os.listdir(constants.UPLOAD_FOLDER)
                    state["job"]["filename"] = filename
                    state["job"]["passesperline"] = jsondata["passes"]
                    state["job"]["laserpower"] = jsondata["laserpower"]
                    # start the print loop
                    asyncio.create_task(print_loop())
        except Exception:
            print("Failed parsing movement request")
        await ws.send(json.dumps(state))


async def print_loop():
    state = constants.MACHINE_STATE
    stopprint = constants.STOP_PRINT
    pauseprint = constants.PAUSE_PRINT
    stopprint.clear()
    pauseprint.clear()
    total_lines = 10
    state["job"]["totallines"] = total_lines
    start_time = time()
    for line in range(total_lines):
        if pauseprint.is_set():
            pauseprint.clear()
            while True:
                await asyncio.sleep(2)
                if stopprint.is_set() or pauseprint.is_set():
                    pauseprint.clear()
                    break
        if constants.STOP_PRINT.is_set():
            constants.STOP_PRINT.clear()
            break
        state["job"]["currentline"] = line + 1
        state["job"]["printingtime"] = round(time() - start_time)
        await asyncio.sleep(5)
    state["printing"] = False


@app.route("/state")
@with_sse
@with_session
async def state(request, session, sse):
    authorized = is_authorized(session)
    if authorized:
        await sse.send(constants.MACHINE_STATE, event="message")
    else:
        await sse.send({"notauthorized": 0}, event="message")


@app.route("/favicon.ico")
async def favicon(request):
    return send_file("static/favicon.webp", max_age=86400)


@app.route("/static/<path:path>")
async def static(request, path):
    if ".." in path:
        # directory traversal is not allowed
        return "Not found", 404
    return send_file("static/" + path, max_age=86400)


if __name__ == "__main__":
    python_files = [f for f in os.listdir("templates") if ".py" in f]
    for f in python_files:
        os.remove("templates/" + f)
    asyncio.run(app.start_server(port=5000, debug=True))
