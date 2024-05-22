import io

import pytest

from rest import WebApp


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
    state = webserver.get_state()
    assert "files" in list(state.keys())


def test_upload_file(webserver):
    fname = "test.pat"
    file = io.StringIO("some initial text data")
    webserver.upload_file(fname, file)
    assert fname in webserver.get_state().get("files")
    webserver.send_command({"command": "deletefile", "file": fname})
    assert fname not in webserver.get_state().get("files")
