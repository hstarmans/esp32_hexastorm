import pytest

from control.camera import camera
from control.webapp import app
from microdot.test_client import TestClient


def test_camera_pins_loaded_from_platform_config():
    """Verify camera pins are read directly from PlatformConfig.esp32_cfg."""
    assert camera.cam_cfg["d0"] == 12
    assert camera.cam_cfg["vsync"] == 41
    assert camera.cam_cfg["href"] == 40
    assert camera.cam_cfg["xclk"] == 39
    assert camera.cam_cfg["reset"] == 42
    assert camera.i2c_cfg["sda"] == 18
    assert camera.i2c_cfg["scl"] == 17
    assert camera.data_pins == [12, 14, 21, 13, 11, 10, 9, 8]


def test_camera_mock_capture():
    """Verify mock capture produces a valid JPEG."""
    data = camera.capture()
    assert data is not None
    assert len(data) > 0
    # Must start with JPEG Start Of Image marker (0xFFD8)
    assert data.startswith(b"\xff\xd8")
    # Must end with JPEG End Of Image marker (0xFFD9)
    assert data.endswith(b"\xff\xd9")


def test_camera_snapshot_endpoint():
    """Verify webserver /api/camera/snapshot endpoint with authorization."""
    import asyncio

    async def run():
        client = TestClient(app)

        # Unauthorized access check (redirects to login)
        res_unauth = await client.get("/api/camera/snapshot")
        assert res_unauth.status_code == 302

        # Authorize session
        await client.post(
            "/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="password=hex",
        )

        # Authorized snapshot check
        res = await client.get("/api/camera/snapshot")
        assert res.status_code == 200
        assert res.headers.get("Content-Type") == "image/jpeg"
        assert res.body.startswith(b"\xff\xd8")
        assert res.body.endswith(b"\xff\xd9")

    asyncio.run(run())
