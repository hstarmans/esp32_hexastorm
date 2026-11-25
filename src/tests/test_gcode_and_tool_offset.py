import asyncio
import copy
import json
import math
import struct
import zlib

import pytest

from control import constants, webapp
from control.laserhead import base as base_mod
from control.laserhead import laserhead
from control.laserhead.base import _linearize_arc_xy, _parse_gcode_words
from microdot.test_client import TestClient


@pytest.fixture(autouse=True)
def isolate_machine_state(tmp_path, monkeypatch):
    """Isolate CONFIG, NVS writes, job folder, and laserhead state across tests."""
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
    monkeypatch.setitem(constants.CONFIG["webserver"], "job_folder", str(tmp_path))

    laserhead._position[:] = 0.0
    laserhead._work_offset[:] = 0.0
    laserhead.enable_steppers = False
    laserhead.reset_state()
    laserhead._stop.clear()
    laserhead._pause.clear()
    webapp.devicestate.update()

    try:
        yield tmp_path
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


def test_parse_gcode_words_and_arc_helpers():
    """Verify G-code comment/compact word parsing and G2/G3 arc linearization."""
    assert _parse_gcode_words("N10 G01X10.5Y-2.5F300 ; cut") == [
        ("G", 1.0),
        ("X", 10.5),
        ("Y", -2.5),
        ("F", 300.0),
    ]
    assert _parse_gcode_words("G1 (mid comment) X5 Y6 (end)") == [
        ("G", 1.0),
        ("X", 5.0),
        ("Y", 6.0),
    ]
    assert _parse_gcode_words("; only a comment") == []

    # Quarter CCW arc around (0, 0) with radius 10 and helical Z
    ccw_pts = _linearize_arc_xy(
        10.0, 0.0, 0.0, 0.0, 10.0, -0.5, is_ccw=True, i_off=-10.0, j_off=0.0
    )
    assert len(ccw_pts) >= 4
    assert ccw_pts[-1] == pytest.approx([0.0, 10.0, -0.5])
    for x, y, _z in ccw_pts:
        assert math.sqrt(x * x + y * y) == pytest.approx(10.0, abs=1e-4)

    # Full 360-deg CW circle around (0, 0)
    circle_pts = _linearize_arc_xy(
        10.0, 0.0, 0.0, 10.0, 0.0, 0.0, is_ccw=False, i_off=-10.0, j_off=0.0
    )
    assert circle_pts[-1] == pytest.approx([10.0, 0.0, 0.0])
    assert min(pt[0] for pt in circle_pts) == pytest.approx(-10.0, abs=0.05)

    # Positive R (minor <= 180 deg) vs negative R (major > 180 deg)
    minor = _linearize_arc_xy(10.0, 0.0, 0.0, 0.0, 10.0, 0.0, is_ccw=True, radius=10.0)
    major = _linearize_arc_xy(10.0, 0.0, 0.0, 0.0, 10.0, 0.0, is_ccw=True, radius=-10.0)
    assert len(major) > len(minor) * 2
    # Major CCW arc from (10, 0) to (0, 10) has center (10, 10) and sweeps > 180 deg through (20, 10) & (10, 20)
    assert max(pt[0] for pt in major) > 19.0
    assert max(pt[1] for pt in major) > 19.0

    with pytest.raises(ValueError, match="diameter"):
        _linearize_arc_xy(0.0, 0.0, 0.0, 10.0, 0.0, 0.0, is_ccw=True, radius=2.0)


def test_execute_gcode_modal_compact_arcs_dwell_and_cleanup(tmp_path, monkeypatch):
    """Verify execute_gcode clears stale flags, runs compact/modal/arc blocks, and cleans up."""
    gcode_file = tmp_path / "board_outline.gcode"
    gcode_file.write_text(
        "\n".join(
            [
                "(Mill PCB outline)",
                "G21 G90",
                "M3 S200",
                "G0 Z2.0",
                "G1X10Y0F600 ; compact token motion",
                "Y10 (modal G1 continues here)",
                "G3 X0 Y20 I-10 J0",
                "G2 X10 Y30 R10.0 Z1.0",
                "G91",
                "G0 X-5 Y-5",
                "G90",
                "G4 P0.01",
                "M5",
                "M30",
                "G0 X99 Y99 ; must not run after M30",
            ]
        ),
        encoding="utf-8",
    )

    # Simulate stale stop/pause flags left over from an earlier abort
    laserhead._stop.set()
    laserhead._pause.set()

    moves = []
    spindle_events = []
    orig_gotopoint = laserhead.gotopoint
    orig_set_spindle = laserhead.set_spindle

    async def spy_gotopoint(
        position,
        speed=None,
        absolute=True,
        check_sensors=False,
        workspace=False,
        validate_limits=True,
    ):
        moves.append((list(position), speed, absolute, workspace))
        # Apply coordinate update instantly without sleeping in 300ms chunks per arc segment
        laserhead._validate_target_position(
            position, absolute=absolute, workspace=workspace
        )
        laserhead._update_coordinates(position, absolute=absolute, workspace=workspace)

    async def spy_spindle(value):
        spindle_events.append(int(value))
        await orig_set_spindle(value)

    monkeypatch.setattr(laserhead, "gotopoint", spy_gotopoint)
    monkeypatch.setattr(laserhead, "set_spindle", spy_spindle)

    asyncio.run(laserhead.execute_gcode("board_outline.gcode", spindle_spinup_s=0.01))

    assert laserhead.state["error_message"] is None
    assert laserhead.state["printing"] is False
    assert laserhead.state["paused"] is False
    assert laserhead.enable_steppers is False
    assert laserhead.wpos == pytest.approx([5.0, 25.0, 1.0])
    assert spindle_events[0] == 200
    assert spindle_events[-1] == 0
    assert all(m[0][:2] != [99.0, 99.0] for m in moves)
    assert laserhead.state["job"]["totallines"] == 15
    assert laserhead.state["job"]["currentline"] >= 14
    monkeypatch.setattr(laserhead, "gotopoint", orig_gotopoint)


def test_execute_gcode_custom_origin_and_error_handling(tmp_path, monkeypatch):
    """Verify use_custom_start, G20 rejection, and cleanup on soft-limit error."""
    async def fast_gotopoint(
        position,
        speed=None,
        absolute=True,
        check_sensors=False,
        workspace=False,
        validate_limits=True,
    ):
        laserhead._validate_target_position(
            position, absolute=absolute, workspace=workspace
        )
        laserhead._update_coordinates(position, absolute=absolute, workspace=workspace)

    monkeypatch.setattr(laserhead, "gotopoint", fast_gotopoint)

    # 1. use_custom_start overrides WPOS zero at job start
    custom_gc = tmp_path / "custom.nc"
    custom_gc.write_text("G90 G0 X10 Y5\n", encoding="utf-8")
    constants.CONFIG["defaultprint"]["use_custom_start"] = True
    constants.CONFIG["defaultprint"]["workspace_origin"] = [12.0, 8.0, 0.0]

    asyncio.run(laserhead.execute_gcode("custom.nc", spindle_spinup_s=0.0))
    assert laserhead.state["error_message"] is None
    assert laserhead.wpos == pytest.approx([10.0, 5.0, 0.0])
    assert laserhead.mpos == pytest.approx([22.0, 13.0, 0.0])

    # 2. G20 inches must abort with clear UI error and turn spindle off
    constants.CONFIG["defaultprint"]["use_custom_start"] = False
    inch_gc = tmp_path / "inch.gcode"
    inch_gc.write_text("M3 S150\nG20\nG1 X1\n", encoding="utf-8")
    asyncio.run(laserhead.execute_gcode("inch.gcode", spindle_spinup_s=0.0))
    assert "G20" in (laserhead.state["error_message"] or "")
    assert laserhead.state["components"]["spindle"] == 0
    assert not laserhead.enable_steppers
    assert not laserhead.state["printing"]

    # 3. Soft-limit error leaves spindle off and printing cleared
    oob_gc = tmp_path / "oob.gcode"
    oob_gc.write_text("M3 S180\nG0 X9999\n", encoding="utf-8")
    asyncio.run(laserhead.execute_gcode("oob.gcode", spindle_spinup_s=0.0))
    assert "exceeds max limit" in (laserhead.state["error_message"] or "")
    assert laserhead.state["components"]["spindle"] == 0
    assert not laserhead.enable_steppers
    assert not laserhead.state["printing"]


def test_apply_laser_offset_print_loop_and_webapp(tmp_path, monkeypatch):
    """Verify apply_laser_offset moves laser to WPOS (-offset_x, -offset_y) and webapp persists flag."""
    # Minimal valid zlib-compressed .pat header with 0 lanes
    pat_file = tmp_path / "pcb_e1_nocor.pat"
    pat_file.write_bytes(zlib.compress(struct.pack("<fII", 5.6, 4, 0)))

    constants.CONFIG["tools"] = {"laser": {"offset_x": -10.0, "offset_y": -10.0}}
    constants.CONFIG["defaultprint"]["home_before_print"] = False
    constants.CONFIG["defaultprint"]["use_custom_start"] = False

    moves = []

    async def fast_gotopoint(
        position,
        speed=None,
        absolute=True,
        check_sensors=False,
        workspace=False,
        validate_limits=True,
    ):
        moves.append(list(position))
        laserhead._validate_target_position(
            position, absolute=absolute, workspace=workspace
        )
        laserhead._update_coordinates(position, absolute=absolute, workspace=workspace)

    async def noop_sleep(_secs):
        return None

    monkeypatch.setattr(laserhead, "gotopoint", fast_gotopoint)
    monkeypatch.setattr(base_mod.asyncio, "sleep", noop_sleep)

    # Disabled: starts and finishes at WPOS (0, 0)
    constants.CONFIG["defaultprint"]["apply_laser_offset"] = False
    asyncio.run(laserhead.print_loop("pcb_e1_nocor.pat"))
    assert moves[0][:2] == pytest.approx([0.0, 0.0])
    assert moves[-1][:2] == pytest.approx([0.0, 0.0])

    # Enabled with laser offset (-10, -10) relative to spindle -> starts at WPOS (+10, +10)
    moves.clear()
    constants.CONFIG["defaultprint"]["apply_laser_offset"] = True
    asyncio.run(laserhead.print_loop("pcb_e1_nocor.pat"))
    assert moves[0][:2] == pytest.approx([10.0, 10.0])
    assert moves[-1][:2] == pytest.approx([0.0, 0.0])

    # Verify /print/control endpoint forwards/persists apply_laser_offset cleanly
    async def run_endpoint():
        monkeypatch.setattr(laserhead, "print_loop", lambda _f: asyncio.sleep(0))
        client = TestClient(webapp.app)
        await client.post(
            "/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="password=hex",
        )
        res = await client.post(
            "/print/control",
            body=json.dumps(
                {
                    "action": "start",
                    "file": "pcb_e1_nocor.pat",
                    "laserpower": 120,
                    "exposureperline": 1,
                    "singlefacet": False,
                    "apply_laser_offset": True,
                    "lanewidth_correction": 0.0,
                    "workspace_origin": [0, 0, 0],
                    "home_before_print": False,
                    "use_custom_start": False,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 200
        assert constants.CONFIG["defaultprint"]["apply_laser_offset"] is True

    asyncio.run(run_endpoint())
