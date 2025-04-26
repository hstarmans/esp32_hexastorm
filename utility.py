"""Shell script for the final configuration of the ESP32S3
after the correct firmware has been flashed.

This script automates post-flashing configuration tasks for the ESP32S3.
It assumes the device has already been flashed with the appropriate binary
and is ready for setup. Use this script to perform actions such as
setting Wi-Fi credentials, configuring network settings, or initializing
application-specific parameters directly on the ESP32S3.
"""

import argparse
import subprocess
from time import sleep

from pathlib import Path


def run(cmd, timeout=1):
    """Older ESP32 require timeout after shell command."""
    subprocess.run(cmd)
    sleep(timeout)


def install(device=None, debug=None, **kwargs):
    base = ["mpremote", "resume"]
    libraries = []
    files = []
    folders = []

    if device:
        base += ["connect", f"{device}"]

    # allows you keep keys in a different file
    if Path("secret.json").is_file():
        print("picked up secret.json")
        files = [Path("secret.json")]

    if not debug:
        input("Copying boot, CTRL+C to abort")
        files += ["src/boot.py"]

    for lib in libraries:
        run(base + ["mip", "install", lib])

    folders = [Path(f) for f in folders]
    for folder in folders:
        run(base + ["fs", "cp", "-r", folder.as_posix(), ":"])

    files = [Path(f) for f in files]
    for file in files:
        if file.name == "secret.json":
            run(base + ["fs", "cp", f"{file}", ":config.json"])
        else:
            run(base + ["fs", "cp", f"{file}", f":{file.name}"])

    run(base + ["reset"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Utility to automate install ESP32"
    )
    parser.add_argument(
        "--device",
        type=str,
        nargs="?",
        help="Give device name, e.g. COM8 or ttyS8 for Windows and linux, respectively.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    install(**vars(args))
