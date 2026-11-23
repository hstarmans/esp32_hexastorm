"""Camera controller module for Hexastorm.

Pulls hardware pin definitions centrally from `PlatformConfig.esp32_cfg`.
Unified controller using the 'camera' module (real on ESP32, mock on CPython).
"""

import logging

from camera import (
    Camera,
    FrameSize,
    GrabMode,
    PixelFormat,
)

try:
    from camera import FRAME_SIZE_DIMS
except ImportError:
    FRAME_SIZE_DIMS = {
        FrameSize.QQVGA: (160, 120),
        FrameSize.QVGA: (320, 240),
        FrameSize.VGA: (640, 480),
        FrameSize.SVGA: (800, 600),
        FrameSize.HD: (1280, 720),
        FrameSize.SXGA: (1280, 1024),
        FrameSize.UXGA: (1600, 1200),
    }
from hexastorm.config import PlatformConfig

logger = logging.getLogger(__name__)

__all__ = [
    "FRAME_SIZE_DIMS",
    "AnalysisModeContext",
    "CameraController",
    "FrameSize",
    "GrabMode",
    "PixelFormat",
    "camera",
]


class AnalysisModeContext:
    """Context manager ensuring safe transition to analysis mode (e.g. SVGA GRAYSCALE)
    and guaranteed restoration of normal Web UI display mode (UXGA JPEG).
    """

    def __init__(
        self,
        controller: "CameraController",
        frame_size: int = FrameSize.SVGA,
        pixel_format: int = PixelFormat.GRAYSCALE,
    ):
        self.controller = controller
        self.target_frame_size = frame_size
        self.target_pixel_format = pixel_format
        self.prev_frame_size = None
        self.prev_pixel_format = None

    def __enter__(self):
        self.prev_frame_size = self.controller.current_frame_size
        self.prev_pixel_format = self.controller.current_pixel_format
        self.controller.reconfigure(
            frame_size=self.target_frame_size,
            pixel_format=self.target_pixel_format,
        )
        self.controller.set_exposure_ms(self.controller.exposure_ms)
        return self.controller

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.controller.reconfigure(
                frame_size=self.prev_frame_size,
                pixel_format=self.prev_pixel_format,
            )
            self.controller.set_exposure_ms(self.controller.exposure_ms)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error restoring camera display mode: {e}")


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
        self.current_frame_size = self.settings.get("frame_size", FrameSize.UXGA)
        self.current_pixel_format = self.settings.get("pixel_format", PixelFormat.JPEG)

        # Camera must be initialized immediately - it must not be None
        self.init_camera()

    def reconfigure(
        self,
        frame_size=None,
        pixel_format=None,
        grab_mode=None,
        fb_count=None,
    ):
        """Reconfigure OV2640 sensor parameters (resolution, pixel format, etc.)."""
        if self.cam is None:
            self.init_camera()

        kwargs = {}
        if frame_size is not None:
            kwargs["frame_size"] = frame_size
        if pixel_format is not None:
            kwargs["pixel_format"] = pixel_format
        if grab_mode is not None:
            kwargs["grab_mode"] = grab_mode
        if fb_count is not None:
            kwargs["fb_count"] = fb_count

        if hasattr(self.cam, "reconfigure"):
            self.cam.reconfigure(**kwargs)

        if frame_size is not None:
            self.current_frame_size = frame_size
        if pixel_format is not None:
            self.current_pixel_format = pixel_format

        logger.debug(
            f"Camera reconfigured: frame_size={self.current_frame_size}, pixel_format={self.current_pixel_format}"
        )

    def analysis_mode(
        self,
        frame_size=FrameSize.SVGA,
        pixel_format=PixelFormat.GRAYSCALE,
    ):
        """Context manager to enter analysis mode and restore web mode on exit."""
        return AnalysisModeContext(
            self, frame_size=frame_size, pixel_format=pixel_format
        )

    def _ensure_cam(self):
        if self.cam is None:
            self.init_camera()
        if self.cam is None:
            raise RuntimeError("Camera initialization failed, self.cam is None")
        return self.cam

    def discard_frame(self):
        """Flush a potentially stale DMA frame buffer before capturing a synchronized exposure."""
        cam = self._ensure_cam()
        try:
            _ = cam.capture()
        finally:
            if hasattr(cam, "free_buffer"):
                cam.free_buffer()

    def capture_grayscale_array(self, frame_size=None):
        """Capture a raw grayscale frame into a 2D ulab.numpy array (h, w).

        Operates in RAM via zero-copy buffer views and frees the camera frame buffer immediately.
        """
        try:
            from ulab import numpy as np
        except ImportError:
            import numpy as np

        cam = self._ensure_cam()

        if frame_size is not None and self.current_frame_size != frame_size:
            self.reconfigure(frame_size=frame_size, pixel_format=PixelFormat.GRAYSCALE)

        w, h = FRAME_SIZE_DIMS.get(self.current_frame_size, (800, 600))
        raw = cam.capture()
        if not raw:
            raise RuntimeError("No frame data returned from camera sensor")

        try:
            # Create a 2D ndarray from the raw byte buffer and copy so buffer can be safely freed
            arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w))
            arr_copy = np.array(arr, dtype=np.uint8)
        finally:
            if hasattr(cam, "free_buffer"):
                cam.free_buffer()

        return arr_copy

    def set_exposure_ms(self, ms: "int | float"):
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
            raise RuntimeError(
                "Camera is None! Cannot configure an uninitialized camera."
            )

        self.exposure_ms = max(1, int(ms))
        self.settings["exposure_ms"] = self.exposure_ms
        # OV2640 registers: aec_value represents sensor integration rows
        # (max 1200 rows in UXGA / 600 rows in SVGA over a ~200 ms frame window).
        _, frame_h = FRAME_SIZE_DIMS.get(self.current_frame_size, (1600, 1200))
        aec = min(frame_h, max(0, int(self.exposure_ms * (frame_h / 200.0))))

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
        except Exception as e:
            self.cam = None
            self.last_error = f"Camera Hardware Init Failed: {e}"
            logger.error(f"Error during camera hardware init: {e}")
            raise

    def capture(self):
        """Capture a JPEG frame from the camera."""
        try:
            cam = self._ensure_cam()

            img = cam.capture()
            if not img:
                raise RuntimeError("No frame data returned from camera sensor")

            data = bytes(img)
            cam.free_buffer()
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
