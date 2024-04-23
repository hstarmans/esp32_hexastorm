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


def authorized(session):
    pwd = session.get("password")
    if isinstance(pwd, type(None)):
        return False
    elif pass_to_sha(pwd) == constants.PASSWORD:
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
    h.update(constants.SECRET_KEY.encode())
    h.update(password.encode())
    return binascii.hexlify(h.digest()).decode()


@app.route("/", methods=["GET", "POST"])
@with_session
async def index(req, session):
    state = constants.MACHINE_STATE
    logged_in = authorized(session)
    if req.method == "POST":
        password = req.form.get("password")
        session["password"] = password
        session.save()
        return redirect("/")
    if logged_in:
        state["files"] = os.listdir(constants.UPLOAD_FOLDER)
        state["wifi"]["connected"] = bootlib.is_connected()
        state["wifi"]["available"] = bootlib.list_wlans()
        return Template("home.html").generate_async(authorized=logged_in, state=state)
    else:
        return Template("login.html").generate_async(authorized=logged_in), 401


@app.post("/upload")
@with_session
async def upload(request, session):
    logged_in = authorized(session)
    folder = constants.UPLOAD_FOLDER
    if logged_in:
        files = os.listdir(folder)
        if (len(files) > 0) & (
            sum(os.stat(folder + "/" + f)[6] for f in files) / (1024 * 1024)
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
            fail = 0
            while size > 0:
                chunk = await request.stream.read(min(size, 1024))
                f.write(chunk)
                size -= len(chunk)
                # no new chunk coming in, user hanged up
                if len(chunk) == 0:
                    fail += 1
                if fail > 10:
                    break
                print(f"processed {size}")
        if fail > 10:
            os.remove(folder + "/" + filename)
            res = {"error": "user hanged up"}, 414
        res = {"success": "upload succeeded"}, 200
    else:
        res = {"unauthorized": "please login"}, 401
    constants.MACHINE_STATE["files"] = os.listdir(constants.UPLOAD_FOLDER)
    return res


@app.get("/logout")
@with_session
async def logout(req, session):
    session.delete()
    return redirect("/")


@app.route("/command")
@with_websocket
@with_session
async def command(request, session, ws):
    state = constants.MACHINE_STATE
    logged_in = authorized(session)
    if not logged_in:
        return redirect("/")

    while logged_in:
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
                    print(f"Deleting {filename}")
                    os.remove(constants.UPLOAD_FOLDER + "/" + filename)
                    state["files"] = os.listdir(constants.UPLOAD_FOLDER)
                elif command == "changewifi":
                    print(
                        f"connecting to {jsondata['wifi']} with {jsondata['password']}"
                    )
                    state["wifi"]["ssid"] = jsondata["wifi"]
                    state["wifi"]["password"] = jsondata["password"]
                elif command == "startwebrepl":
                    bootlib.start_webrepl()
                    request.app.shutdown()
                elif command == "startprint":
                    filename = jsondata["file"].replace("/", "_")
                    constants.MACHINE_STATE = constants.init_state()
                    # relink state
                    state = constants.MACHINE_STATE
                    state["printing"] = True
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
    logged_in = authorized(session)
    if logged_in:
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
