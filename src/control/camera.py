"""Camera controller module for Hexastorm.

Pulls hardware pin definitions centrally from `PlatformConfig.esp32_cfg`.
Unified controller using the 'camera' module (real on ESP32, mock on CPython).
"""

import logging

from camera import Camera, FrameSize, GrabMode, PixelFormat
from hexastorm.config import PlatformConfig

logger = logging.getLogger(__name__)


class CameraController:
    """Unified Camera Controller for Hexastorm."""

    def __init__(self):
        self.cfg = PlatformConfig(test=False)
        self.cam_cfg = self.cfg.esp32_cfg["camera"]
        self.i2c_cfg = self.cfg.esp32_cfg["i2c"]

        self.data_pins = [
            self.cam_cfg["d0"],
            self.cam_cfg["d1"],
            self.cam_cfg["d2"],
            self.cam_cfg["d3"],
            self.cam_cfg["d4"],
            self.cam_cfg["d5"],
            self.cam_cfg["d6"],
            self.cam_cfg["d7"],
        ]
        self.vsync_pin = self.cam_cfg["vsync"]
        self.href_pin = self.cam_cfg["href"]
        self.pclk_pin = self.cam_cfg["pclk"]
        self.xclk_pin = self.cam_cfg["xclk"]
        self.pwdn_pin = self.cam_cfg.get("pwdn", -1)
        self.reset_pin = self.cam_cfg.get("reset", -1)
        self.sda_pin = self.i2c_cfg["sda"]
        self.scl_pin = self.i2c_cfg["scl"]
        self.cam = None
        self.last_error = None

    def init_camera(self):
        """Initialize camera hardware or mock instance."""
        if self.cam is not None:
            return True

        try:
            logger.info(
                f"Initializing camera (D0={self.data_pins[0]}, SDA={self.sda_pin}, SCL={self.scl_pin})..."
            )
            self.cam = Camera(
                data_pins=self.data_pins,
                vsync_pin=self.vsync_pin,
                href_pin=self.href_pin,
                sda_pin=self.sda_pin,
                scl_pin=self.scl_pin,
                pclk_pin=self.pclk_pin,
                xclk_pin=self.xclk_pin,
                xclk_freq=10000000,
                pixel_format=PixelFormat.JPEG,
                frame_size=FrameSize.UXGA,
                jpeg_quality=85,
                fb_count=1,
                grab_mode=GrabMode.LATEST,
                reset_pin=self.reset_pin,
                powerdown_pin=self.pwdn_pin,
            )
            self.cam.exposure_ctrl = False
            self.cam.gain_ctrl = False
            self.cam.aec_value = 150
            self.cam.agc_gain = 0
            self.last_error = None
            logger.info("Camera initialized successfully.")
            return True
        except Exception as e:  # noqa: BLE001, catch hardware errors safely
            self.cam = None
            self.last_error = f"Camera Hardware Init Failed: {e}"
            logger.error(f"Error during camera hardware init: {e}")
            raise

    def capture(self):
        """Capture a JPEG frame from the camera."""
        try:
            if self.cam is None:
                self.init_camera()

            img = self.cam.capture()
            if not img:
                raise RuntimeError("No frame data returned from camera sensor")

            data = bytes(img)
            self.cam.free_buffer()
            self.last_error = None
            return data
        except Exception as e:  # noqa: BLE001, catch all capture errors to prevent server crash
            self.last_error = f"Camera Capture Error: {e}"
            logger.error(f"Error during camera capture: {e}")
            if self.cam is not None and hasattr(self.cam, "deinit"):
                try:
                    self.cam.deinit()
                except Exception as deinit_err:  # noqa: BLE001
                    logger.error(f"Error deinitializing camera: {deinit_err}")
            self.cam = None
            return None


camera = CameraController()
