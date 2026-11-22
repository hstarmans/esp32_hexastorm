import asyncio
import copy
import json
import re
from pathlib import Path

import pytest

from control import constants, webapp
from control.laserhead import base as base_mod
from control.laserhead import laserhead
from microdot.test_client import TestClient


@pytest.fixture(autouse=True)
def isolate_webapp_state(monkeypatch):
    """Isolate CONFIG, NVS writes, and laserhead state across endpoint tests."""
    orig_cfg = copy.deepcopy(constants.CONFIG)
    orig_pos = laserhead._position.copy()
    orig_woff = laserhead._work_offset.copy()
    orig_steppers = laserhead.enable_steppers
    orig_state = copy.deepcopy(laserhead._state)
    orig_stop_set = laserhead._stop.is_set()
    orig_pause_set = laserhead._pause.is_set()

    monkeypatch.setattr(webapp, "update_config", lambda: None)
    monkeypatch.setattr(constants, "update_config", lambda: None)
    monkeypatch.setattr(constants.NVS_STORE, "_save_mock", lambda: None)
    monkeypatch.setattr(base_mod.NVS_STORE, "_save_mock", lambda: None)

    laserhead._position[:] = 0.0
    laserhead._work_offset[:] = 0.0
    laserhead.enable_steppers = False
    laserhead.state["printing"] = False
    laserhead.state["paused"] = False
    laserhead.state["estop"] = False
    laserhead.state["error_message"] = None
    laserhead.state["components"].update(
        {
            "laser": False,
            "diodetest": None,
            "rotating": False,
            "spindle": 0,
            "fan": 0,
        }
    )
    laserhead._stop.clear()
    laserhead._pause.clear()
    webapp.devicestate.update()

    try:
        yield
    finally:
        constants.CONFIG.clear()
        constants.CONFIG.update(orig_cfg)
        laserhead._position[:] = orig_pos
        laserhead._work_offset[:] = orig_woff
        laserhead.enable_steppers = orig_steppers
        laserhead._state.clear()
        laserhead._state.update(orig_state)
        if orig_stop_set:
            laserhead._stop.set()
        else:
            laserhead._stop.clear()
        if orig_pause_set:
            laserhead._pause.set()
        else:
            laserhead._pause.clear()
        webapp.devicestate.update()


async def login(client: TestClient, password: str = "hex"):
    """Authenticate a Microdot TestClient session via POST /."""
    return await client.post(
        "/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=f"password={password}",
    )


def test_auth_login_logout_and_template_render():
    """Verify login/logout session flow, 401/302 guards, and home.html UI rendering."""

    async def run():
        client = TestClient(webapp.app)

        res_login_page = await client.get("/")
        assert res_login_page.status_code == 200
        assert "Enter System" in res_login_page.text
        assert 'name="password"' in res_login_page.text

        unauth_api_calls = [
            ("GET", "/api/settings", None),
            ("POST", "/gotopoint", {"position": [1, 1, 0], "absolute": True, "workspace": False}),
            ("POST", "/control/laser", {}),
            ("POST", "/upload", b"123"),
            ("POST", "/deletefile", {"file": "missing.pat"}),
        ]
        for method, path, payload in unauth_api_calls:
            res_unauth = await client.request(method, path, body=payload)
            assert res_unauth.status_code == 401
            assert res_unauth.json == {"error": "Unauthorized"}

        res_unauth_logout = await client.get("/logout")
        assert res_unauth_logout.status_code == 302
        assert res_unauth_logout.headers.get("Location") == "/"

        res_bad_login = await login(client, password="wrong")
        assert res_bad_login.status_code == 302
        res_still_unauth = await client.get("/api/settings")
        assert res_still_unauth.status_code == 401

        res_ok_login = await login(client, password="hex")
        assert res_ok_login.status_code == 302

        res_home = await client.get("/")
        assert res_home.status_code == 200
        assert 'id="cameraPanel"' in res_home.text
        assert 'x-data="movement"' in res_home.text
        assert 'x-data="commands"' in res_home.text
        assert 'x-data="printLauncher"' in res_home.text
        assert "static/home.js" in res_home.text

        res_logout = await client.get("/logout")
        assert res_logout.status_code == 302
        res_after_logout = await client.get("/api/settings")
        assert res_after_logout.status_code == 401

    asyncio.run(run())


def test_motion_workspace_and_estop_endpoints():
    """Verify gotopoint, soft limits, clearerror, workspace zero, home, and E-stop lock."""

    async def run():
        client = TestClient(webapp.app)
        await login(client)

        res_move = await client.post(
            "/gotopoint",
            body=json.dumps(
                {"position": [10.0, 5.0, 1.0], "absolute": True, "workspace": False}
            ),
            headers={"Content-Type": "application/json"},
        )
        assert res_move.status_code == 200
        assert res_move.json["mpos"] == pytest.approx([10.0, 5.0, 1.0])

        res_zero = await client.post(
            "/setworkspacezero",
            body=json.dumps({"axes": [1, 1, 0]}),
            headers={"Content-Type": "application/json"},
        )
        assert res_zero.status_code == 200
        assert res_zero.json["wpos"][:2] == pytest.approx([0.0, 0.0])

        res_home = await client.post(
            "/home",
            body=json.dumps({"axes": [1, 0, 0]}),
            headers={"Content-Type": "application/json"},
        )
        assert res_home.status_code == 200
        assert res_home.json["mpos"][0] == pytest.approx(0.0)

        res_oob = await client.post(
            "/gotopoint",
            body=json.dumps(
                {"position": [9999.0, 0.0, 0.0], "absolute": True, "workspace": False}
            ),
            headers={"Content-Type": "application/json"},
        )
        assert res_oob.status_code == 400
        assert "error" in res_oob.json
        assert laserhead.state["error_message"] is not None

        res_clear = await client.post("/clearerror")
        assert res_clear.status_code == 200
        assert res_clear.json["error_message"] is None

        res_abort = await client.post("/control/abort")
        assert res_abort.status_code == 200
        assert res_abort.json["estop"] is True

        res_locked = await client.post(
            "/gotopoint",
            body=json.dumps(
                {"position": [5.0, 5.0, 0.0], "absolute": True, "workspace": False}
            ),
            headers={"Content-Type": "application/json"},
        )
        assert res_locked.status_code == 400
        assert "E-STOP" in res_locked.json["error"]

        res_reset = await client.post("/control/reset")
        assert res_reset.status_code == 200
        assert res_reset.json["estop"] is False
        assert res_reset.json["error_message"] is None

    asyncio.run(run())


def test_peripheral_controls_and_printing_guards(monkeypatch):
    """Verify peripheral controls, facet mean persistence, and 409 guards while printing."""
    diodetest_calls = []

    async def fake_test_diode():
        diodetest_calls.append(True)

    monkeypatch.setattr(laserhead, "test_diode", fake_test_diode)

    async def run():
        client = TestClient(webapp.app)
        await login(client)

        res_laser = await client.post("/control/laser")
        assert res_laser.status_code == 200
        assert res_laser.json["components"]["laser"] is True

        res_prism = await client.post("/control/prism")
        assert res_prism.status_code == 200
        assert res_prism.json["components"]["rotating"] is True

        res_spindle = await client.post(
            "/control/spindle",
            body=json.dumps({"value": 128}),
            headers={"Content-Type": "application/json"},
        )
        assert res_spindle.status_code == 200
        assert res_spindle.json["components"]["spindle"] == 128

        res_fan = await client.post(
            "/control/fan",
            body=json.dumps({"value": 200}),
            headers={"Content-Type": "application/json"},
        )
        assert res_fan.status_code == 200
        assert res_fan.json["components"]["fan"] == 200

        res_save_empty = await client.post("/control/save_facet_means")
        assert res_save_empty.status_code == 400

        laserhead.state["components"]["diodetest"] = {
            "facets": {
                "0": {"mean_ms": 5.01},
                "1": {"mean_ms": 5.02},
                "2": {"mean_ms": 4.99},
                "3": {"mean_ms": 5.00},
            }
        }
        res_save_ok = await client.post("/control/save_facet_means")
        assert res_save_ok.status_code == 200
        assert constants.CONFIG["laserhead"]["facetmeans"] == [5.01, 5.02, 4.99, 5.00]
        assert res_save_ok.json["components"]["diodetest"]["facets"]["0"]["delta_mean_ms"] == 0.0

        res_diode = await client.post("/control/diodetest")
        assert res_diode.status_code == 200
        assert res_diode.json == {"status": "started"}
        await asyncio.sleep(0)
        assert len(diodetest_calls) == 1

        laserhead.state["printing"] = True
        for path, body in (
            ("/control/laser", {}),
            ("/control/prism", {}),
            ("/control/diodetest", {}),
            ("/control/save_facet_means", {}),
            (
                "/print/control",
                {
                    "action": "start",
                    "file": "job_e1_nocor.pat",
                    "laserpower": 100,
                    "exposureperline": 1,
                    "singlefacet": False,
                    "lanewidth_correction": 0.0,
                    "workspace_origin": [0, 0, 0],
                    "home_before_print": False,
                    "use_custom_start": False,
                },
            ),
        ):
            res_busy = await client.post(
                path,
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
            assert res_busy.status_code == 409

    asyncio.run(run())


def test_upload_and_delete_file_endpoints(tmp_path, monkeypatch):
    """Verify /upload validation, path sanitization, quota enforcement, and /deletefile."""
    monkeypatch.setitem(constants.CONFIG["webserver"], "job_folder", str(tmp_path))

    async def run():
        client = TestClient(webapp.app)
        await login(client)

        res_empty = await client.post(
            "/upload",
            headers={"Content-Disposition": 'attachment; filename="empty.pat"'},
            body=b"",
        )
        assert res_empty.status_code == 400

        res_no_name = await client.post(
            "/upload",
            headers={"Content-Type": "application/octet-stream"},
            body=b"payload",
        )
        assert res_no_name.status_code == 400

        payload = b"HEXASTORM_PAT_DATA_1234"
        res_ok = await client.post(
            "/upload",
            headers={
                "Content-Disposition": 'attachment; filename="../../evil dir/board_e1_nocor.pat"',
                "Content-Type": "application/octet-stream",
            },
            body=payload,
        )
        assert res_ok.status_code == 200
        expected_file = tmp_path / "board_e1_nocor.pat"
        assert expected_file.is_file()
        assert expected_file.read_bytes() == payload

        monkeypatch.setitem(
            constants.CONFIG["webserver"], "max_content_length", 0.000001
        )
        res_quota = await client.post(
            "/upload",
            headers={"Content-Disposition": 'attachment; filename="too_big.pat"'},
            body=b"X" * 64,
        )
        assert res_quota.status_code == 413

        res_del_missing_param = await client.post(
            "/deletefile",
            body=json.dumps({}),
            headers={"Content-Type": "application/json"},
        )
        assert res_del_missing_param.status_code == 400

        res_del_404 = await client.post(
            "/deletefile",
            body=json.dumps({"file": "does_not_exist.pat"}),
            headers={"Content-Type": "application/json"},
        )
        assert res_del_404.status_code == 404

        res_del_ok = await client.post(
            "/deletefile",
            body=json.dumps({"file": "board_e1_nocor.pat"}),
            headers={"Content-Type": "application/json"},
        )
        assert res_del_ok.status_code == 200
        assert not expected_file.exists()

    asyncio.run(run())


def test_print_control_and_settings_and_ota_check(monkeypatch):
    """Verify /print/control lifecycle, /api/settings GET/POST, and /api/system/update/check."""
    started_files = []
    motor_apply_calls = []

    async def fake_print_loop(fname):
        started_files.append(fname)

    monkeypatch.setattr(laserhead, "print_loop", fake_print_loop)
    monkeypatch.setattr(
        laserhead, "apply_motor_settings", lambda: motor_apply_calls.append(True)
    )

    async def run():
        client = TestClient(webapp.app)
        await login(client)

        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        assert set(res_get.json.keys()) == {
            "wifi_login",
            "motors",
            "tools",
            "defaultprint",
            "calibration",
        }

        updated_defaultprint = dict(res_get.json["defaultprint"])
        updated_defaultprint["lanewidth_correction"] = 0.25
        res_save = await client.post(
            "/api/settings",
            body=json.dumps({"defaultprint": updated_defaultprint}),
            headers={"Content-Type": "application/json"},
        )
        assert res_save.status_code == 200
        assert res_save.json["status"] == "success"
        assert constants.CONFIG["defaultprint"]["lanewidth_correction"] == 0.25
        assert len(motor_apply_calls) == 1

        res_start = await client.post(
            "/print/control",
            body=json.dumps(
                {
                    "action": "start",
                    "file": "subdir/board_e1_nocor.pat",
                    "laserpower": 110,
                    "exposureperline": 2,
                    "singlefacet": False,
                    "lanewidth_correction": 0.1,
                    "workspace_origin": [1.0, 2.0, 0.0],
                    "home_before_print": False,
                    "use_custom_start": False,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        assert res_start.status_code == 200
        await asyncio.sleep(0)
        assert started_files == ["subdir_board_e1_nocor.pat"]

        res_pause = await client.post(
            "/print/control",
            body=json.dumps({"action": "pause"}),
            headers={"Content-Type": "application/json"},
        )
        assert res_pause.status_code == 200
        assert res_pause.json["paused"] is True

        res_stop = await client.post(
            "/print/control",
            body=json.dumps({"action": "stop"}),
            headers={"Content-Type": "application/json"},
        )
        assert res_stop.status_code == 200
        assert laserhead._stop.is_set() is True

        monkeypatch.setattr(
            webapp.bootlib,
            "get_firmware_dct",
            lambda require_new=True: {"tag_name": "v9.9.9"},
        )
        res_upd_new = await client.get("/api/system/update/check")
        assert res_upd_new.status_code == 200
        assert res_upd_new.json["update_available"] is True
        assert res_upd_new.json["latest_version"] == "v9.9.9"

        monkeypatch.setattr(
            webapp.bootlib,
            "get_firmware_dct",
            lambda require_new=True: {},
        )
        res_upd_none = await client.get("/api/system/update/check")
        assert res_upd_none.status_code == 200
        assert res_upd_none.json["update_available"] is False

    asyncio.run(run())


def test_static_files_and_path_traversal():
    """Verify public /static/ serving and rejection of '..' traversal attempts."""

    async def run():
        client = TestClient(webapp.app)

        res_js = await client.get("/static/home.js")
        assert res_js.status_code == 200
        assert "Alpine.store" in res_js.text

        res_trav = await client.get("/static/../config.json")
        assert res_trav.status_code == 404

    asyncio.run(run())


def test_frontend_routes_match_registered_webapp_routes():
    """Verify every endpoint referenced in home.js and HTML templates exists in app.url_map."""
    root_dir = Path(__file__).resolve().parents[1] / "root"
    sources = [(root_dir / "static" / "home.js").read_text()]
    for html_path in sorted((root_dir / "templates").glob("*.html")):
        sources.append(html_path.read_text())

    combined = "\n".join(sources)
    patterns = [
        re.compile(r"""fetch\(\s*['"]([^'"?]+)"""),
        re.compile(r"""(?:window\.)?api\.post\(\s*['"]([^'"?]+)"""),
        re.compile(r"""xhr\.open\(\s*['"][A-Z]+['"]\s*,\s*['"]([^'"?]+)"""),
        re.compile(r"""EventSource\(\s*['"]([^'"?]+)"""),
        re.compile(r"""<a[^>]+href=['"](/[^'"]*?)['"]"""),
    ]
    extracted_paths = set()
    for pat in patterns:
        extracted_paths.update(pat.findall(combined))

    assert len(extracted_paths) >= 20
    missing = [
        path
        for path in sorted(extracted_paths)
        if not any(
            url_pattern.match(path) is not None
            for _, url_pattern, *_ in webapp.app.url_map
        )
    ]
    assert not missing, f"Frontend references unregistered routes: {missing}"
