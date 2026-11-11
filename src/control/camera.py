"""Camera controller module for Hexastorm.

Pulls hardware pin definitions centrally from `PlatformConfig.esp32_cfg`.
Unified controller using the 'camera' module (real on ESP32, mock on CPython).
"""

import logging

from camera import Camera
from hexastorm.config import PlatformConfig

logger = logging.getLogger(__name__)


class CameraController:
    """Unified Camera Controller for Hexastorm."""

    def __init__(self):
        self.cfg = PlatformConfig(test=False)
        self.cam_cfg = self.cfg.esp32_cfg["camera"]
        self.settings = self.cam_cfg["settings"]
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
        self.xclk_freq = self.cam_cfg["xclk_freq"]
        self.pwdn_pin = self.cam_cfg["pwdn"]
        self.reset_pin = self.cam_cfg["reset"]
        self.sda_pin = self.i2c_cfg["sda"]
        self.scl_pin = self.i2c_cfg["scl"]
        self.cam = None
        self.last_error = None
        self.exposure_ms = self.settings["exposure_ms"]

        # Camera must be initialized immediately - it must not be None
        self.init_camera()

    def set_exposure_ms(self, ms: int):
        """Configure manual camera exposure in milliseconds and disable all automatics.

        The OV2640 hardware sensor regulates shutter time in sensor line integration
        periods ('aec_value', 0 to 1200 rows in UXGA mode). At 10 MHz XCLK in UXGA
        (1600x1200), a full 1200-row frame integrates over ~200 ms (approx. 6 lines
        per ms). We map ms linearly to aec_value clamped to [0, 1200].

        Additionally, all automatic DSP routines (AEC, AGC, AWB, BPC, WPC) are
        strictly disabled to prevent beam blowout, color distortion, or spot removal.

        :param ms: Desired exposure integration time in milliseconds (e.g. 5, 50, 200).
        """
        if self.cam is None:
            raise RuntimeError("Camera is None! Cannot configure an uninitialized camera.")

        self.exposure_ms = max(1, int(ms))
        self.settings["exposure_ms"] = self.exposure_ms
        # OV2640 registers: aec_value represents integration lines (0 - 1200 in UXGA)
        # 1200 lines / ~200 ms frame time ~= 6 lines per ms
        aec = min(1200, max(0, int(self.exposure_ms * 6)))

        # 1. Disable Auto Exposure Control and set line integration time
        self.cam.exposure_ctrl = False
        self.cam.aec2 = False
        self.cam.aec_value = aec

        # 2. Disable Auto Gain Control & lock gain at 0
        self.cam.gain_ctrl = False
        self.cam.agc_gain = 0

        # 3. Disable Auto White Balance
        self.cam.whitebal = False
        self.cam.awb_gain = False

        # 4. Disable DSP filters (BPC / WPC)
        self.cam.bpc = False
        self.cam.wpc = False

        logger.info(
            f"Camera optical registers applied: exposure={self.exposure_ms}ms (aec={aec}), AEC/AGC/AWB=OFF"
        )

    def init_camera(self):
        """Initialize camera hardware or mock instance."""
        if self.cam is not None:
            return True

        try:
            import gc

            gc.collect()
        except ImportError:
            pass

        try:
            logger.info(
                f"Initializing camera (D0={self.data_pins[0]}, SDA={self.sda_pin}, SCL={self.scl_pin}, XCLK_FREQ={self.xclk_freq})..."
            )
            self.cam = Camera(
                data_pins=self.data_pins,
                vsync_pin=self.vsync_pin,
                href_pin=self.href_pin,
                sda_pin=self.sda_pin,
                scl_pin=self.scl_pin,
                pclk_pin=self.pclk_pin,
                xclk_pin=self.xclk_pin,
                xclk_freq=self.xclk_freq,
                pixel_format=self.settings["pixel_format"],
                frame_size=self.settings["frame_size"],
                jpeg_quality=self.settings["jpeg_quality"],
                fb_count=self.settings["fb_count"],
                grab_mode=self.settings["grab_mode"],
                reset_pin=self.reset_pin,
                powerdown_pin=self.pwdn_pin,
            )

            if self.cam is None:
                raise RuntimeError("Camera initialization failed, self.cam is None")

            # Apply strict optical laser calibration settings (no automatics!)
            from .constants import CONFIG

            exp_ms = CONFIG["calibration"]["camera_exposure_ms"]
            self.set_exposure_ms(exp_ms)

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
            self.last_error = f"{e}"
            logger.error(f"Error during camera capture: {e}")
            if self.cam is not None:
                try:
                    self.cam.deinit()
                except Exception as deinit_err:  # noqa: BLE001
                    logger.error(f"Error deinitializing camera: {deinit_err}")
            self.cam = None
            return None


camera = CameraController()
