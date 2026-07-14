import json
import logging
import sys
import os


ESP32 = False if sys.platform in ["linux", "win32", "darwin"] else True
logger = logging.getLogger(__name__)


class SafeNVS:
    """
    Unified Key-Value store using ESP32 Non-Volatile Storage (NVS).
    Falls back to a local JSON file on PC (development/mock mode).
    """

    def __init__(self, namespace="hexastorm"):
        self.esp32_nvs = None
        self.mock_file = "nvs_mock.json" if ESP32 else "src/root/nvs_mock.json"
        self.mock_data = {}

        if ESP32:
            try:
                from esp32 import NVS

                self.esp32_nvs = NVS(namespace)
            except ImportError:
                logger.error("Could not import esp32.NVS despite being on ESP32.")
        else:
            self._load_mock()

    def _load_mock(self):
        try:
            with open(self.mock_file) as f:
                self.mock_data = json.load(f)
        except OSError:
            self.mock_data = {}

    def _save_mock(self):
        try:
            with open(self.mock_file, "w") as f:
                json.dump(self.mock_data, f)
        except OSError as e:
            logger.error(f"Failed to write mock NVS file: {e}")

    def set_int(self, key, value):
        """Write an integer to NVS buffer."""
        if self.esp32_nvs:
            self.esp32_nvs.set_i32(key, int(value))
        else:
            self.mock_data[key] = int(value)

    def get_int(self, key, default=0):
        """Retrieve an integer from NVS, returning default if key doesn't exist."""
        if self.esp32_nvs:
            try:
                return self.esp32_nvs.get_i32(key)
            except OSError:  # Key not found raises OSError
                return default
        else:
            return self.mock_data.get(key, default)

    def commit(self):
        """Flush all pending changes to the physical flash memory."""
        if self.esp32_nvs:
            self.esp32_nvs.commit()
        else:
            self._save_mock()

    def save_state(self, mpos, woff):
        """
        Helper to efficiently save 3D coordinates (mpos & woff) in millimeters.
        Converts floats to thousandths (microns) to store as 32-bit integers.
        """
        axes = ["x", "y", "z"]
        for i, axis in enumerate(axes):
            self.set_int(f"mpos_{axis}", int(mpos[i] * 1000))
            self.set_int(f"woff_{axis}", int(woff[i] * 1000))
        self.commit()


def deploy_assets(overwrite=False):
    """Extracts frozen assets only if a sentinel file is missing."""

    # Check for sentinel file (fastest check)
    if not overwrite:
        try:
            os.stat("/templates/home.html")
            logging.info("Assets already deployed. Skipping extraction.")
            return
        except OSError:
            pass  # File missing, proceed to extract

    logging.info("First boot detected. Initializing asset extraction...")

    # Once you import frozen_root, the on-import hooks will run
    # files get extracted and overwrite
    try:
        from . import frozen_root
    except ImportError:
        logging.error("Could not import frozen_root. Is the build correct?")
        return

    # Perform the extraction
    logging.info("Extracting static files to filesystem...")


def recurse_dct(dct, target, replace):
    for key, val in dct.items():
        if isinstance(val, dict):
            recurse_dct(val, target, replace)
        elif isinstance(val, str):
            dct[key] = val.replace(target, replace)


def load_config():
    """Load json config settings."""
    fname = "config.json" if ESP32 else "src/root/config.json"
    try:
        with open(fname) as f:
            dct = json.load(f)
            if not ESP32:
                recurse_dct(dct, "sd/", "src/root/sd/")
    except OSError:
        logger.warning("Could not load config.json, using defaults")
        dct = {}
    return dct


def update_config():
    """Update the json settings."""
    fname = "config.json" if ESP32 else "src/root/config.json"
    with open(fname, "w") as fp:
        if not ESP32:
            recurse_dct(CONFIG, "src/root/sd/", "sd/")
            json.dump(CONFIG, fp, indent=4)
            recurse_dct(CONFIG, "sd/", "src/root/sd/")
        else:
            # micropython doesn't support indent
            json.dump(CONFIG, fp, separators=(",\n", ":\n"))


if ESP32:
    deploy_assets()  # ensure config exists
CONFIG = load_config()
NVS_STORE = SafeNVS()
