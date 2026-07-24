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
    """Extracts frozen assets if firmware BUILD_ID differs from deployed version on disk."""

    try:
        from . import build_info
    except ImportError:
        logger.error("Could not import build_info. Is the build correct?")
        return

    current_build_id = build_info.BUILD_ID

    # Read currently deployed build ID from disk
    deployed_build_id = None
    if not overwrite:
        try:
            with open(".asset_version", "r") as f:
                deployed_build_id = f.read().strip()
        except OSError:
            pass

    # If build IDs match and CONFIG_FILE exists, skip extraction
    if not overwrite and current_build_id and deployed_build_id == current_build_id:
        try:
            os.stat(CONFIG_FILE)
            logger.info("Assets up to date. Skipping extraction.")
            return
        except OSError:
            pass

    logger.info(
        f"New firmware build detected ({current_build_id or 'first boot'}). Deploying assets..."
    )

    # Back up active config to a temporary file
    has_old_config = False
    try:
        os.stat(CONFIG_FILE)
        try:
            os.rename(CONFIG_FILE, CONFIG_OLD_FILE)
            has_old_config = True
            logger.info(
                f"Moved {CONFIG_FILE} to {CONFIG_OLD_FILE} before asset extraction."
            )
        except OSError:
            pass
    except OSError:
        pass

    # Import frozen_root to trigger extraction (this extracts a fresh factory config.json)
    try:
        if "control.frozen_root" in sys.modules:
            del sys.modules["control.frozen_root"]
        from . import frozen_root
    except Exception as e:
        logger.error(f"Asset extraction failed: {e}")
        return

    # Merge user settings back into the newly extracted config.json
    if has_old_config:
        try:
            # Load new factory defaults
            with open(CONFIG_FILE, "r") as f_new:
                new_dct = json.load(f_new)
            
            # Load old user config
            with open(CONFIG_OLD_FILE, "r") as f_old:
                old_dct = json.load(f_old)
                
            logger.info("Migrating user settings into new configuration file...")
            new_dct = merge_configs(new_dct, old_dct)
            
            # Save the merged config back to config.json
            with open(CONFIG_FILE, "w") as f_out:
                json.dump(new_dct, f_out)
                
            # Remove the temp backup after successful merge
            os.remove(CONFIG_OLD_FILE)
        except Exception as e:
            logger.error(f"Failed to merge old configuration: {e}")

    # Record deployed build ID
    if current_build_id:
        try:
            with open(".asset_version", "w") as f:
                f.write(str(current_build_id))
        except OSError as e:
            logger.error(f"Could not save .asset_version: {e}")


def recurse_dct(dct, target, replace):
    for key, val in dct.items():
        if isinstance(val, dict):
            recurse_dct(val, target, replace)
        elif isinstance(val, str):
            dct[key] = val.replace(target, replace)


def load_config():
    """Load json config settings."""

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

    return dct


def sanitize_types(obj):
    """
    Recursively converts string representations of numbers and booleans
    received from web forms into native Python types (int, float, bool).

    :param obj: The object, list, or primitive value to sanitize.
    :return: The structure with sanitized data types.
    """
    if isinstance(obj, dict):
        return {k: sanitize_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_types(v) for v in obj]
    elif isinstance(obj, str):
        val = obj.strip()
        if val.lower() == "true":
            return True
        if val.lower() == "false":
            return False
        try:
            return int(val)
        except ValueError:
            try:
                return float(val)
            except ValueError:
                return val  # Retain standard strings (e.g., SSIDs or file paths)
    return obj


def dump_pretty_json(obj, fp, indent=4, level=0):
    """Custom pretty-printer for MicroPython to handle proper indentation."""
    spacing = " " * (indent * level)
    if isinstance(obj, dict):
        if not obj:
            fp.write("{}")
            return
        fp.write("{\n")
        items = list(obj.items())
        for i, (k, v) in enumerate(items):
            fp.write(" " * (indent * (level + 1)))
            fp.write(f'"{k}": ')
            dump_pretty_json(v, fp, indent, level + 1)
            if i < len(items) - 1:
                fp.write(",")
            fp.write("\n")
        fp.write(spacing + "}")
    elif isinstance(obj, list):
        # Render simple primitive lists (e.g. [0, 0, 0]) on a single line
        if all(not isinstance(x, (dict, list)) for x in obj):
            json.dump(obj, fp)
        else:
            fp.write("[\n")
            for i, v in enumerate(obj):
                fp.write(" " * (indent * (level + 1)))
                dump_pretty_json(v, fp, indent, level + 1)
                if i < len(obj) - 1:
                    fp.write(",")
                fp.write("\n")
            fp.write(spacing + "]")
    else:
        json.dump(obj, fp)


def update_config():
    """Update the json settings."""
    global CONFIG

    clean_cfg = sanitize_types(CONFIG)
    CONFIG.clear()
    CONFIG.update(clean_cfg)

    with open(CONFIG_FILE, "w") as fp:
        if not ESP32:
            recurse_dct(CONFIG, "src/root/sd/", "sd/")
            json.dump(CONFIG, fp, indent=4)
            recurse_dct(CONFIG, "sd/", "src/root/sd/")
        else:
            dump_pretty_json(CONFIG, fp, indent=4)


if ESP32:
    deploy_assets()  # Extracts fresh config.json if missing

# Load config
CONFIG = load_config()

NVS_STORE = SafeNVS()
