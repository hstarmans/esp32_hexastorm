import requests
import json
from websocket import create_connection, enableTrace
import subprocess
import os
import time
import io


import pytest

# https://github.com/mpetazzoni/sseclient
from sseclient import SSEClient


base = "localhost:5000"
base_url = f"http://{base}/"


def login(key_dct=None):
    if key_dct is None:
        key_dct = {"password": "wachtwoord"}
    session = requests.Session()
    response = session.post(base_url, data=key_dct)
    return response, session


@pytest.fixture
def webserver(scope="session", sleep=1):
    # webserver only started once, i.e. scope session
    # one second sleep ensure is online
    class server:
        def __init__(self):
            dirname = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
            self.process = subprocess.Popen(["micropython", "webapp.py"], cwd=dirname)
            time.sleep(1)

        def __del__(self):
            self.process.kill()

    return server()


@pytest.fixture
def str_cookie(webserver):
    _, session = login()
    return "".join([f"{k}={v}" for k, v in session.cookies.get_dict().items()])


def send_command(str_cookie, command=None):
    if command is None:
        command = {"command": "toggleprism"}
    #   debug via webtrace
    #   look at request headers webapp (webapp.request.header)
    #   socketio is not yet supported by microdot
    enableTrace(False)
    ws = create_connection(f"ws://{base}/command", cookie=str_cookie)
    ws.send(json.dumps(command))
    return json.loads(ws.recv())


def test_login(webserver):
    response, _ = login()
    assert response.status_code == 200
    response, _ = login({"password": "invalidkey"})
    assert response.status_code == 401


def test_websocket(str_cookie):
    # Send commands via websocket
    status = send_command(str_cookie)
    assert "files" in list(status.keys())


def test_sseclient(str_cookie):
    def with_requests(url, headers):
        """Get a streaming response for the given event feed using requests."""
        return requests.get(url, stream=True, headers=headers)

    headers = {"Accept": "text/event-stream", "Cookie": str_cookie}
    response = with_requests(base_url + "state", headers)
    client = SSEClient(response)
    for event in client.events():
        print(json.loads(event.data))


def test_upload_file(webserver, str_cookie):
    _, session = login()
    fname = "test.txt"

    file = io.StringIO("some initial text data")

    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": f"attachment; filename={fname}",
    }
    session.post(base_url + "upload", files={"file": (fname, file)}, headers=headers)
    time.sleep(1)
    assert fname in send_command(str_cookie)["files"]
    send_command(str_cookie, command={"command": "deletefile", "file": fname})
    assert fname not in send_command(str_cookie)["files"]
