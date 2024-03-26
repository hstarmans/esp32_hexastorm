import asyncio
import random
from time import time
import json
import os

from microdot import Microdot, Response, redirect, send_file, Request
from microdot.websocket import with_websocket
from microdot.session import Session, with_session
from microdot.utemplate import Template
from microdot.sse import with_sse

import bootlib
import constants


app = Microdot()
Session(app, secret_key="top-secret")
Response.default_content_type = "text/html"
# 10MB requests allowed
Request.max_content_length = 10 * 1024 * 1024


def is_authorized(session):
    password = session.get("password")
    if password == "wachtwoord":
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
    if req.method == "POST":
        password = req.form.get("password")
        session["password"] = password
        session.save()
        return redirect("/")
    if authorized:
        files = os.listdir("files")
        return Template("home.html").render(authorized=authorized, files=files)
    else:
        return Template("login.html").render(authorized=authorized)


@app.post("/upload")
@with_session
async def upload(request, session):
    authorized = is_authorized(session)
    if authorized:
        if (
            sum(os.stat("files/" + f)[6] for f in os.listdir("files")) / (1024 * 1024)
            > 15
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
        with open("files/" + filename, "wb") as f:
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
                if command == "toggleprism":
                    state["rotating"] = not state["rotating"]
                    print(f"Change rotation state prism to {state['rotating']}")
                elif command == "togglelaser":
                    state["laser"] = not state["laser"]
                    print(f"Laser on is {state['laser']}")
                elif command == "diodetest":
                    state["diodetest"] = None
                    await ws.send(json.dumps(state))
                    await asyncio.sleep(3)
                    state["diodetest"] = True if random.randint(0, 10) > 5 else False
                    print(f"Diode test is {state['diodetest']}")
                elif command == "move":
                    steps = float(jsondata["steps"])
                    vector = [int(x) for x in jsondata["vector"]]
                    print(f"Moving {steps} along {vector}")
                elif command == "deletefile":
                    filename = jsondata["file"].replace("/", "_")
                    os.remove("files/" + filename)
                elif command == "startprint":
                    filename = jsondata["file"].replace("/", "_")
                    passes = jsondata["passes"]
                    laserpower = jsondata["laserpower"]
                    state = {
                        "printing": True,
                        "rotating": False,
                        "laser": False,
                        "diodetest": None,
                        "filename": filename,
                        "currentline": 0,
                        "passesperline": passes,
                        "laserpower": laserpower,
                        "totallines": 0,
                        "printingtime": 0,
                    }
                    # fix link with MACHINE_STATE
                    constants.MACHINE_STATE = state
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
    state["totallines"] = total_lines
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
        state["currentline"] = line + 1
        state["printingtime"] = round(time() - start_time)
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
