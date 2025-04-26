import os
import subprocess
import json
import time
from pathlib import Path

import requests
from websocket import create_connection, enableTrace
from sseclient import SSEClient


class WebApp:
    """class to interact with ESP32 hexastorm webserver
    
    This is not the main webapp. It creates a webserver when
    you start it. You can then connect to it using the other 
    methods.
    As such it can be use for debugging. 
    """
    def __init__(self):
        # export IP="192.168.1.8",
        # PORT=5000,
        # WEBAPP="~/python/esp32_hexastorm"
        ip = os.environ.get("IP")
        if ip is None:
            ip = "localhost"
            port = 5000
            self.webappdir = Path(__file__).resolve().parents[1]
        else:
            port = os.environ.get("PORT")
            self.webappdir = os.environ.get("WEBAPP")
        if ip == "localhost":
            self.process = subprocess.Popen(
                ["uv", "run", "python", "-m", "src.control.webapp"],
                cwd=self.webappdir,
            )
            time.sleep(1)
        else:
            self.process = None
        self.base = f"{ip}:{port}"
        self.base_url = f"http://{self.base}/"
        self.session = self.login()

        self.str_cookie = self.str_cookie()

    def __del__(self):
        if self.process:
            self.process.kill()

    def str_cookie(self):
        return "".join(
            [f"{k}={v}" for k, v in self.session.cookies.get_dict().items()]
        )

    def login(self, password="wachtwoord"):
        session = requests.Session()
        response = session.post(
            self.base_url, data={"password": password}, timeout=3
        )
        if not response.ok:
            raise Exception("Cannot login invalid password")
        return session

    def get_state(self):
        def with_requests(url, headers):
            """Get a streaming response for the given event feed using request."""
            return requests.get(url, stream=True, headers=headers)

        headers = {"Accept": "text/event-stream", "Cookie": self.str_cookie}
        response = with_requests(self.base_url + "state", headers)
        client = SSEClient(response)
        for event in client.events():
            return json.loads(event.data)

    def send_command(self, command):
        #   debug via webtrace
        #   look at request headers webapp (webapp.request.header)
        #   socketio is not yet supported by microdot
        enableTrace(False)
        ws = create_connection(
            f"ws://{self.base}/command", cookie=self.str_cookie
        )
        ws.send(json.dumps(command))
        return json.loads(ws.recv())

    def upload_file(self, fname, file):
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f"attachment; filename={fname}",
        }
        print(self.base_url + "upload")
        self.session.post(
            self.base_url + "upload",
            files={"file": (fname, file)},
            headers=headers,
        )
        time.sleep(2)
