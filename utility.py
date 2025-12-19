"""
ESP32S3 Configuration Utility

This script automates the final provisioning step for ESP32S3 devices running MicroPython.
It specifically looks for a local 'secret.json' file and securely transfers it to the
device filesystem as 'config.json'.

Workflow:
    1. Scans the local directory for 'secret.json'.
    2. Connects to the ESP32S3 via 'mpremote'.
    3. Uploads the file to the root directory as ':config.json'.
    4. Issues a hardware reset to the device to initialize the new configuration.

Usage:
    uv run utility.py --device <PORT> [--debug]

Prerequisites:
    - mpremote (pip install mpremote)
    - ESP32S3 with MicroPython firmware already flashed.
"""

import argparse
import subprocess
import sys
import logging
from pathlib import Path

# Configure logging: Time - Level - Message
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_mpremote(args, device=None):
    """Constructs and runs an mpremote command with error capturing."""
    cmd = ["mpremote"]
    if device:
        cmd += ["connect", device]

    cmd += ["resume"] + args

    try:
        logger.debug(f"Running command: {' '.join(cmd)}")
        # capture_output keeps the terminal clean unless an error occurs
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"mpremote failed (Exit {e.returncode})")
        if e.stderr:
            logger.error(f"Stderr: {e.stderr.strip()}")
        sys.exit(1)
    except FileNotFoundError:
        logger.critical("mpremote not found. Is it installed in your environment?")
        sys.exit(1)


def configure_device(device=None, debug=False):
    """Handles the file discovery and upload process."""
    if debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug mode enabled. Verbose output active.")

    secret_file = Path("secret.json")

    if not secret_file.is_file():
        logger.warning(f"File '{secret_file}' not found. No configuration to upload.")
        return

    logger.info(f"Source file found: {secret_file}")

    if not debug:
        try:
            input(
                "Ready to upload to ESP32S3. Press Enter to continue (Ctrl+C to abort)... "
            )
        except KeyboardInterrupt:
            logger.info("Operation cancelled by user.")
            sys.exit(0)

    # Copy secret.json -> :config.json
    logger.info("Uploading secret.json as config.json...")
    run_mpremote(["fs", "cp", str(secret_file), ":config.json"], device)

    # Reset the device
    logger.info("Upload complete. Performing hardware reset...")
    run_mpremote(["reset"], device)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Provision ESP32S3 with config.json using mpremote."
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Serial port (e.g., COM3 or /dev/ttyACM0). If omitted, mpremote attempts auto-detect.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show verbose debug logs and skip confirmation prompts.",
    )

    args = parser.parse_args()
    configure_device(device=args.device, debug=args.debug)
