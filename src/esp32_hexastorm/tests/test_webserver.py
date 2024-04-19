import json
import time
import io

import requests

import pytest

# https://github.com/mpetazzoni/sseclient
from sseclient import SSEClient

from esp32_hexastorm.rest import WebApp


@pytest.fixture
def webserver(scope="session"):
    return WebApp()


def test_wronglogin(webserver):
    try:
        webserver.login("wachtwoord")
    except Exception:
        assert False
    with pytest.raises(Exception):
        webserver.login("falsepassword")


def test_websocket(webserver):
    # Send commands via websocket
    status = webserver.send_command({"command": "toggleprism"})
    assert "files" in list(status.keys())


def test_sseclient(webserver):
    def with_requests(url, headers):
        """Get a streaming response for the given event feed using requests."""
        return requests.get(url, stream=True, headers=headers)

    headers = {"Accept": "text/event-stream", "Cookie": webserver.str_cookie}
    response = with_requests(webserver.base_url + "state", headers)
    client = SSEClient(response)
    for event in client.events():
        assert "files" in list(json.loads(event.data).keys())


def test_upload_file(webserver):
    fname = "test.pat"
    file = io.StringIO("some initial text data")
    webserver.upload_file(fname, file)
    assert fname in webserver.send_command({"command": "toggleprism"})["files"]
    webserver.send_command({"command": "deletefile", "file": fname})
    assert fname not in webserver.send_command({"command": "toggleprism"})["files"]
