import asyncio
import random
from time import localtime
import json

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
            if command == "toggleprism":
                state["rotating"] = not state["rotating"]
                print(f"Change rotation state prism to {state['rotating']}")
            elif command == "togglelaser":
                state["laser"] = not state["laser"]
                print(f"Laser on is {state['laser']}")
            elif command == "diodetest":
                state["diodetest"] = None
                await ws.send(json.dumps(constants.MACHINE_STATE))
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
            print(f"received command {command}")
        except Exception:
            print("Failed parsing movement request")
        await ws.send(json.dumps(constants.MACHINE_STATE))


async def log_current():
    while True:
        t_loc = localtime()
        t_stamp = f"{t_loc[0]}-{t_loc[1]}-{t_loc[2]} {t_loc[3]}:{t_loc[4]}:{t_loc[5]}"
        MEASUREMENT = [
            t_stamp,
            random.randint(1, 10),
            random.randint(1, 10),
            random.randint(1, 10),
        ]
        constants.MEASUREMENT = MEASUREMENT
        await asyncio.sleep(5)


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


async def main():
    logger_task = asyncio.create_task(log_current())
    server_task = asyncio.create_task(app.start_server(port=5000, debug=True))
    await asyncio.gather(logger_task, server_task)


if __name__ == "__main__":
    # remove rendered python files
    import os

    python_files = [f for f in os.listdir("templates") if ".py" in f]
    for f in python_files:
        os.remove("templates/" + f)
    asyncio.run(main())
    import os
