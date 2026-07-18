import json
import logging
import sys
import os


ESP32 = False if sys.platform in ["linux", "win32", "darwin"] else True
logger = logging.getLogger(__name__)

if ESP32:
    CONFIG_FILE = "config.json"
    CONFIG_OLD_FILE = "config_old.json"
    NVS_FILE = "nvs_mock.json"  # Unused usually, but keeps variables consistent
else:
    CONFIG_FILE = "src/root/mock_config.json"
    FACTORY_CONFIG_FILE = "src/root/config.json"  # Immutable template
    CONFIG_OLD_FILE = "src/root/mock_config_old.json"
    NVS_FILE = "src/root/nvs_mock.json"


class SafeNVS:
    """
    Unified Key-Value store using ESP32 Non-Volatile Storage (NVS).
    Falls back to a local JSON file on PC (development/mock mode).
    """

    def __init__(self, namespace="hexastorm"):
        self.esp32_nvs = None
        self.mock_file = NVS_FILE
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


def merge_configs(default_dct, old_dct):
    """
    Recursively updates default_dct with values from old_dct.
    Preserves new keys in default_dct that don't exist in old_dct.
    """
    for key, value in old_dct.items():
        # If the value is a dictionary, and the key exists in both, recurse
        if (
            isinstance(value, dict)
            and key in default_dct
            and isinstance(default_dct[key], dict)
        ):
            merge_configs(default_dct[key], value)
        else:
            # Otherwise, overwrite the default with the user's old value
            default_dct[key] = value
    return default_dct


def deploy_assets(overwrite=False):
    """Extracts frozen assets only if a sentinel file is missing."""

    # Check for sentinel file (fastest check)
    if not overwrite:
        try:
            os.stat("/templates/config.json")
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
    """Load json config settings and migrate old configs if present."""

    # Recreate mock_config.json from factory template if missing ---
    if not ESP32:
        try:
            os.stat(CONFIG_FILE)
        except OSError:
            logger.info(
                "mock_config.json missing. Generating from factory config.json..."
            )
            try:
                with open(FACTORY_CONFIG_FILE, "r") as src:
                    with open(CONFIG_FILE, "w") as dst:
                        dst.write(src.read())
            except OSError:
                logger.error("Could not find factory config.json to copy!")

    # Load the active config (which might be a fresh factory extraction)
    try:
        with open(CONFIG_FILE) as f:
            dct = json.load(f)
            if not ESP32:
                recurse_dct(dct, "sd/", "src/root/sd/")
    except OSError:
        logger.warning(f"Could not load {CONFIG_FILE}, using defaults")
        dct = {}

    # Check if a migration/old config exists
    try:
        with open(CONFIG_OLD_FILE) as f_old:
            logger.info(f"Found {CONFIG_OLD_FILE}. Migrating user settings...")
            old_dct = json.load(f_old)
            dct = merge_configs(dct, old_dct)

        try:
            os.rename(CONFIG_OLD_FILE, CONFIG_OLD_FILE + ".bak")
        except OSError:
            os.remove(CONFIG_OLD_FILE)

        migration_happened = True
    except OSError:
        migration_happened = False

    return dct, migration_happened


def update_config():
    """Update the json settings."""
    with open(CONFIG_FILE, "w") as fp:
        if not ESP32:
            recurse_dct(CONFIG, "src/root/sd/", "sd/")
            json.dump(CONFIG, fp, indent=4)
            recurse_dct(CONFIG, "sd/", "src/root/sd/")
        else:
            json.dump(CONFIG, fp, separators=(",\n", ":\n"))


if ESP32:
    deploy_assets()  # Extracts fresh config.json if missing

# Load config and capture the migration flag
CONFIG, _migrated = load_config()

if _migrated:
    # Save the newly merged configuration to disk permanently
    logger.info("Saving migrated configuration to disk.")
    update_config()

NVS_STORE = SafeNVS()
