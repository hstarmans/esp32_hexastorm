import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time

import serial
import serial.tools.list_ports

# Ensure src/, vendor/, and hexastorm/src/ are resolvable before importing control/hexastorm
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from control._paths import configure_cpython_paths, resolve_hexastorm_src  # noqa: E402

configure_cpython_paths()

from hexastorm.esp32_controller import ESP32Controller  # noqa: E402
from hexastorm.log_setup import configure_logging  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)

# --- Configuration Paths ---
HOME_DIR = os.path.expanduser("~")
BOARD_NAME = "HEXASTORM_S3"
DEFAULT_DOCKER_IMAGE = "hexastorm-esp32s3-builder:v5.5.2-mp1.28.0"
DOCKER_CACHE_VOLUME = "hexastorm-esp-cache"


def resolve_build_paths(micropython_dir=None, idf_path=None, use_docker=False):
    """Resolve MicroPython, ESP-IDF, board, manifest, and output paths.

    CLI arguments override ``MICROPYTHON_DIR`` / ``IDF_PATH`` environment variables,
    which fall back to ``~/Documents/Python/{micropython,esp-idf}``. When
    ``use_docker=True``, firmware artifacts are written to ``build/<BOARD_NAME>``.
    """
    repo_root = os.path.dirname(SRC_DIR)
    board_dir = os.path.join(repo_root, "boards", BOARD_NAME)

    mp_raw = (
        micropython_dir
        or os.environ.get("MICROPYTHON_DIR")
        or os.path.join(HOME_DIR, "Documents", "Python", "micropython")
    )
    mp_expanded = os.path.abspath(os.path.expanduser(mp_raw))
    if (
        os.path.basename(mp_expanded) == "esp32"
        and os.path.basename(os.path.dirname(mp_expanded)) == "ports"
    ):
        esp32_port_dir = mp_expanded
    else:
        esp32_port_dir = os.path.join(mp_expanded, "ports", "esp32")

    idf_raw = (
        idf_path
        or os.environ.get("IDF_PATH")
        or os.path.join(HOME_DIR, "Documents", "Python", "esp-idf")
    )
    idf_expanded = os.path.abspath(os.path.expanduser(idf_raw))

    local_build_dir = (
        os.path.join(repo_root, "build", BOARD_NAME)
        if use_docker
        else os.path.join(esp32_port_dir, f"build-{BOARD_NAME}")
    )

    return {
        "repo_root": repo_root,
        "board_name": BOARD_NAME,
        "board_dir": board_dir,
        "esp32_port_dir": esp32_port_dir,
        "idf_export_path": os.path.join(idf_expanded, "export.sh"),
        "manifest_path": os.path.join(SRC_DIR, "manifest.py"),
        "cmake_path": os.path.join(SRC_DIR, "micropython.cmake"),
        "local_build_dir": local_build_dir,
    }


def build_docker_run_command(
    paths,
    image_name=DEFAULT_DOCKER_IMAGE,
    hexastorm_dir=None,
    host_uid=None,
    host_gid=None,
    firmware_version=None,
    fpga_bit=None,
    rebuild_fpga=False,
):
    """Construct the ``docker run`` invocation that compiles firmware into ``build/``."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{paths['repo_root']}:/workspace",
        "-v",
        f"{DOCKER_CACHE_VOLUME}:/root/.cache",
        "-e",
        "IDF_GIT_SAFE_DIR=/workspace:/opt/micropython",
    ]

    hs_raw = hexastorm_dir if hexastorm_dir is not None else os.environ.get("HEXASTORM_DIR")
    if hs_raw:
        abs_hs = os.path.abspath(os.path.expanduser(hs_raw))
        cmd.extend(
            [
                "-v",
                f"{abs_hs}:/hexastorm_override:ro",
                "-e",
                "HEXASTORM_DIR=/hexastorm_override",
            ]
        )

    fpga_bit_raw = (
        fpga_bit if fpga_bit is not None else os.environ.get("FPGA_BITSTREAM")
    )
    if fpga_bit_raw:
        abs_bit = os.path.abspath(os.path.expanduser(fpga_bit_raw))
        cmd.extend(
            [
                "-v",
                f"{abs_bit}:/fpga_override.bit:ro",
                "-e",
                "FPGA_BITSTREAM=/fpga_override.bit",
            ]
        )

    if rebuild_fpga:
        cmd.extend(["-e", "REBUILD_FPGA=1"])

    fw_ver = (
        firmware_version
        if firmware_version is not None
        else os.environ.get("FIRMWARE_VERSION")
    )
    if fw_ver:
        cmd.extend(["-e", f"FIRMWARE_VERSION={fw_ver}"])

    if host_uid is not None and host_gid is not None:
        cmd.extend(["-e", f"HOST_UID={host_uid}", "-e", f"HOST_GID={host_gid}"])

    cmd.append(image_name)
    return cmd


def ensure_docker_image(repo_root, image_name=DEFAULT_DOCKER_IMAGE, rebuild=False):
    """Verify Docker is installed and build the firmware builder image if missing."""
    if shutil.which("docker") is None:
        logger.error("Docker executable not found in PATH. Install Docker to use --docker.")
        sys.exit(1)

    if not rebuild:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if inspect.returncode == 0:
            return

    logger.info(f"Building Docker builder image {image_name}...")
    subprocess.run(
        ["docker", "build", "-t", image_name, repo_root],
        check=True,
    )


def build_local_flash_command(active_port, local_build_dir):
    """Construct the host ``python -m esptool`` flash command list."""
    return [
        sys.executable,
        "-m",
        "esptool",
        "--chip",
        "esp32s3",
        "-p",
        active_port,
        "-b",
        "460800",
        "--before=default_reset",
        "--after=watchdog_reset",
        "write_flash",
        "--flash_mode",
        "dio",
        "--flash_freq",
        "80m",
        "--flash_size",
        "32MB",
        "0x0",
        os.path.join(local_build_dir, "bootloader", "bootloader.bin"),
        "0x10000",
        os.path.join(local_build_dir, "micropython.bin"),
        "0x8000",
        os.path.join(local_build_dir, "partition_table", "partition-table.bin"),
        "0xd000",
        os.path.join(local_build_dir, "ota_data_initial.bin"),
    ]


def find_espressif_device(port_arg=None):
    """
    Finds the Espressif device.
    If port_arg is provided, checks that specific port.
    Otherwise, scans for a device with Espressif VID (0x303A).
    Returns (port_device, pid).
    """
    ports = list(serial.tools.list_ports.comports())

    if port_arg:
        for p in ports:
            if p.device == port_arg:
                return p.device, p.pid
        return port_arg, None  # Port exists but maybe not USB, or we can't get PID

    # Auto-detect
    for p in ports:
        if p.vid == 0x303A:  # Espressif VID
            return p.device, p.pid

    return None, None


def find_process_using_port(port):
    """
    Attempts to identify any process (other than current process) holding the port open on Linux.
    Returns tuple of (pid, process_name) or (None, None).
    """
    try:
        my_pid = os.getpid()
        real_port = os.path.realpath(port)
        if not os.path.exists("/proc"):
            return None, None

        for pid_str in os.listdir("/proc"):
            if not pid_str.isdigit():
                continue
            proc_pid = int(pid_str)
            if proc_pid == my_pid:
                continue

            fd_dir = os.path.join("/proc", pid_str, "fd")
            if not os.path.exists(fd_dir):
                continue

            try:
                for fd in os.listdir(fd_dir):
                    fd_path = os.path.join(fd_dir, fd)
                    try:
                        if os.path.realpath(fd_path) == real_port:
                            cmdline_path = os.path.join("/proc", pid_str, "cmdline")
                            comm_path = os.path.join("/proc", pid_str, "comm")
                            name = None
                            if os.path.exists(comm_path):
                                with open(comm_path, "r") as f:
                                    name = f.read().strip()
                            if not name and os.path.exists(cmdline_path):
                                with open(cmdline_path, "r") as f:
                                    name = f.read().replace("\x00", " ").strip()
                            return proc_pid, name or f"PID {proc_pid}"
                    except OSError:
                        pass
            except OSError:
                pass
    except OSError:
        pass
    return None, None


def check_port_availability(port):
    """
    Verifies that the serial port is not currently in use by another application.
    Exits with a clear error message if the port is occupied.
    """
    proc_pid, proc_name = find_process_using_port(port)
    if proc_pid:
        logger.error(
            f"Serial port {port} is currently in use by '{proc_name}' (PID {proc_pid})."
        )
        logger.error(
            "Please close Thonny or any active serial monitor/terminal connected to the board and try again."
        )
        sys.exit(1)

    try:
        s = serial.Serial()
        s.port = port
        s.exclusive = True
        s.open()
        s.close()
    except (serial.SerialException, OSError) as e:
        logger.error(f"Cannot open serial port {port}: {e}")
        logger.error(
            "The port appears to be locked or in use by another application (e.g. Thonny)."
        )
        logger.error("Please close any application using the board and try again.")
        sys.exit(1)


def verify_and_prepare_device(port_arg=None):
    """
    Checks if an Espressif device is connected.
    If it's in MicroPython mode (PID 0x4001), attempts to connect.
    If connection fails due to port in use, aborts.
    If connection fails due to unresponsive device, attempts recovery reset trick.
    If connection succeeds, reboots it into bootloader.
    If it's in Bootloader mode (PID 0x1001), proceeds.
    Returns the port to use, or exits if not possible.
    """
    port, pid = find_espressif_device(port_arg)

    if not port:
        logger.error("No Espressif device found.")
        sys.exit(1)

    logger.info(f"Found device on port {port} with PID {pid}")

    if not os.path.exists(port):
        logger.error(f"Serial port {port} does not exist.")
        sys.exit(1)

    check_port_availability(port)

    # PID 0x1001 (4097) is USB JTAG/serial debug unit (Bootloader)
    # PID 0x4001 (16385) is Espressif Device (usually TinyUSB / MicroPython)

    if pid == 0x1001:
        logger.info("Device is already in bootloader/debug mode. Ready to flash.")
        return port

    if pid == 0x4001 or pid is None:
        logger.info("Device appears to be in MicroPython mode. Attempting to connect...")
        try:
            ctrl = ESP32Controller(port=port, timeout=1.0)
            logger.info(
                "Successfully connected to MicroPython REPL. Rebooting device to bootloader..."
            )
            try:
                ctrl.exec_no_wait("import machine; machine.bootloader()")
            except Exception:  # noqa: BLE001, S110
                pass
            ctrl.close()
        except serial.SerialException as e:
            logger.error(f"Cannot access serial port {port}: {e}")
            logger.error(
                "The port is currently in use by another application (e.g., Thonny)."
            )
            logger.error(
                "Please close Thonny or any serial monitor connected to the device and try again."
            )
            sys.exit(1)
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Failed to get a prompt from the device: {e}. It is unresponsive!"
            )
            logger.warning("\n*** RECOVERY TRICK INITIATED ***")
            logger.warning("Attempting automatic hardware reset via DTR/RTS...")

            try:
                with serial.Serial(port, 115200) as s:
                    s.dtr = False
                    s.rts = True
                    time.sleep(0.1)
                    s.dtr = True
                    s.rts = False
                    time.sleep(0.1)
                    s.dtr = False
                    s.rts = False
            except (serial.SerialException, OSError):
                pass

            time.sleep(0.5)
            if os.path.exists(port):
                # The port didn't drop, meaning the hardware reset failed (old TinyUSB firmware)
                logger.error(
                    "Automatic hardware reset failed (expected on the first run)."
                )
                logger.warning(
                    "Please press the physical RESET button on your board NOW!"
                )
            else:
                logger.info("Hardware reset successful! Reconnecting...")

            logger.info("I will spam Ctrl-C to catch it before it wedges again...")

            start_time = time.time()
            caught = False
            while time.time() - start_time < 15:
                # Wait for port to drop and come back if they press reset
                if not os.path.exists(port):
                    time.sleep(0.1)
                    continue
                try:
                    with serial.Serial(port, 115200, timeout=0.1, write_timeout=0.1) as s:
                        s.write(b"\r\x03\r\x03\r\x03")
                        s.write(b"\x01")
                        response = s.read(100)
                        if b"raw REPL" in response:
                            logger.info("Caught the REPL before it wedged!")
                            s.write(b"import machine; machine.bootloader()\x04")
                            caught = True
                            break
                except (serial.SerialException, OSError):
                    pass
                time.sleep(0.05)

            if not caught:
                logger.error(
                    "Failed to catch the board. Please try running build again and resetting faster."
                )
                sys.exit(1)

        logger.info("Waiting for the board to enter bootloader mode (PID 0x1001)...")
        timeout = 10
        start_time = time.time()
        while time.time() - start_time < timeout:
            new_port, new_pid = find_espressif_device(port_arg)
            if new_pid == 0x1001:
                logger.info(
                    f"Board successfully entered bootloader mode on {new_port}!"
                )
                # Give the OS a tiny bit more time to settle the serial port
                time.sleep(1.0)
                return new_port
            time.sleep(0.5)

        logger.error("Timed out waiting for the board to enter bootloader mode.")
        logger.error("Please reset the device manually into bootloader mode.")
        sys.exit(1)

    logger.warning(f"Unknown PID {pid}. Assuming we can flash.")
    return port


def run_remote_ssh(remote_host, command_str, check=True, capture_output=True):
    """Executes a shell command on the remote host via SSH."""
    ssh_cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        remote_host,
        command_str,
    ]
    return subprocess.run(
        ssh_cmd, check=check, capture_output=capture_output, text=True
    )


def run_remote_python(remote_host, script_content):
    """Executes a Python script on the remote host via SSH stdin."""
    ssh_cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        remote_host,
        "python3 -",
    ]
    return subprocess.run(
        ssh_cmd, input=script_content, text=True, capture_output=True, check=False
    )


def verify_and_prepare_remote_device(remote_host, port_arg=None):
    """
    Checks if an Espressif device is connected on the remote host over SSH.
    Prepares it by rebooting into bootloader mode if needed.
    Returns the port name on the remote host.
    """
    port_arg_repr = repr(port_arg) if port_arg else "None"
    remote_script = f"""
import sys, os, time, serial, serial.tools.list_ports

port_arg = {port_arg_repr}
ports = list(serial.tools.list_ports.comports())

active_port = None
pid = None

if port_arg:
    for p in ports:
        if p.device == port_arg:
            active_port, pid = p.device, p.pid
            break
    if not active_port:
        active_port, pid = port_arg, None
else:
    for p in ports:
        if p.vid == 0x303A:
            active_port, pid = p.device, p.pid
            break

if not active_port:
    print("NO_DEVICE")
    sys.exit(1)

if pid == 0x1001:
    print(f"READY:{{active_port}}")
    sys.exit(0)

# Device is in MicroPython mode (0x4001) or unknown. Reboot to bootloader via REPL.
try:
    with serial.Serial(active_port, 115200, timeout=1.0) as s:
        for _ in range(3):
            s.write(b"\\r\\x03")
            time.sleep(0.1)
            s.write(b"\\x01")
            time.sleep(0.1)
        s.write(b"import machine; machine.bootloader()\\x04")
        time.sleep(0.5)
except Exception:
    pass

# Poll for device to enter bootloader mode (PID 0x1001 or port reset)
start_time = time.time()
while time.time() - start_time < 10:
    time.sleep(0.5)
    p_list = list(serial.tools.list_ports.comports())
    for p in p_list:
        if p.device == active_port and p.pid == 0x1001:
            print(f"READY:{{active_port}}")
            sys.exit(0)

if os.path.exists(active_port):
    print(f"READY:{{active_port}}")
    sys.exit(0)

print("TIMEOUT")
sys.exit(1)
"""

    logger.info(f"Connecting to remote target {remote_host}...")
    res = run_remote_python(remote_host, remote_script)

    if res.returncode != 0:
        if "NO_DEVICE" in res.stdout:
            logger.error(f"No Espressif device found on remote host {remote_host}.")
        else:
            logger.error(
                f"Failed to prepare remote device on {remote_host}: {res.stderr or res.stdout}"
            )
        sys.exit(1)

    for line in res.stdout.splitlines():
        if line.startswith("READY:"):
            remote_port = line.split(":", 1)[1].strip()
            logger.info(f"Remote device on {remote_host} ready on port {remote_port}")
            return remote_port

    logger.error(f"Unexpected response from remote pre-flight check: {res.stdout}")
    sys.exit(1)


def transfer_binaries_remote(
    remote_host, local_build_dir, remote_dir="/tmp/esp32_build"
):
    """
    Transfers the compiled ESP32 firmware binaries from local_build_dir to remote_host:remote_dir.
    """
    logger.info(f"Preparing remote directory {remote_dir} on {remote_host}...")
    run_remote_ssh(remote_host, f"mkdir -p {remote_dir}")

    bins = [
        "bootloader/bootloader.bin",
        "micropython.bin",
        "partition_table/partition-table.bin",
        "ota_data_initial.bin",
    ]
    local_files = [os.path.join(local_build_dir, b) for b in bins]

    for f in local_files:
        if not os.path.exists(f):
            logger.error(f"Required build artifact missing: {f}")
            sys.exit(1)

    logger.info(f"Transferring firmware binaries to {remote_host}:{remote_dir}...")
    scp_cmd = ["scp", "-q", *local_files, f"{remote_host}:{remote_dir}/"]
    res = subprocess.run(scp_cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        logger.error(f"Failed to transfer binaries over scp: {res.stderr}")
        sys.exit(1)


def _validate_submodule_deps(repo_root: str) -> None:
    """Verify tracked submodules in deps/ and hexastorm source are checked out."""
    required = [
        os.path.join(repo_root, "deps", "micropython-ulab", "code", "micropython.cmake"),
        os.path.join(repo_root, "deps", "micropython-camera-API", "micropython.cmake"),
        os.path.join(repo_root, "deps", "esp32-camera", "CMakeLists.txt"),
    ]
    missing = [p for p in required if not os.path.isfile(p)]
    if missing:
        logger.error(
            "Missing submodule dependency files:\n  "
            + "\n  ".join(missing)
            + "\nRun: git submodule update --init --recursive"
        )
        sys.exit(1)

    try:
        hs_src = resolve_hexastorm_src(repo_root)
        logger.info(f"Using hexastorm source: {hs_src}")
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)


def prepare_native_fpga_bitstream(repo_root, fpga_bit=None, rebuild_fpga=False):
    """Ensure src/root/sd/fpga/fpga.bit is staged before a native firmware build."""
    fpga_out = os.path.join(repo_root, "src", "root", "sd", "fpga", "fpga.bit")
    fpga_build_dir = os.path.join(repo_root, "build", "fpga")
    build_fpga_script = os.path.join(repo_root, "docker", "build_fpga.py")
    fpga_bit_raw = (
        fpga_bit if fpga_bit is not None else os.environ.get("FPGA_BITSTREAM")
    )

    if fpga_bit_raw:
        abs_bit = os.path.abspath(os.path.expanduser(fpga_bit_raw))
        if not os.path.isfile(abs_bit) or os.path.getsize(abs_bit) == 0:
            logger.error(f"FPGA bitstream override not found or empty: {abs_bit}")
            sys.exit(1)
        os.makedirs(os.path.dirname(fpga_out), exist_ok=True)
        shutil.copy2(abs_bit, fpga_out)
        logger.info(f"Copied FPGA bitstream override -> {fpga_out}")
        return fpga_out

    has_bit = os.path.isfile(fpga_out) and os.path.getsize(fpga_out) > 0
    if not rebuild_fpga and has_bit:
        return fpga_out

    hs_src = resolve_hexastorm_src(repo_root)
    hs_repo = (
        os.path.dirname(hs_src) if os.path.basename(hs_src) == "src" else str(hs_src)
    )
    if os.path.isfile(os.path.join(hs_repo, "pyproject.toml")) and shutil.which("uv"):
        cmd = [
            "uv",
            "--project",
            hs_repo,
            "run",
            "python",
            build_fpga_script,
            "--workspace",
            repo_root,
            "--build-dir",
            fpga_build_dir,
            "--output",
            fpga_out,
        ]
        if rebuild_fpga:
            cmd.append("--rebuild")
        logger.info("--- Synthesizing FPGA Bitstream (Native via hexastorm uv) ---")
        subprocess.run(cmd, check=True)
        return fpga_out

    logger.error(
        f"Missing FPGA bitstream at {fpga_out} and cannot synthesize natively. "
        "Run 'uv run build --docker' or pass '--fpga-bit <path>'."
    )
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build and flash ESP32 firmware")
    parser.add_argument(
        "--port",
        "-p",
        default=None,
        help="Serial port of the ESP32 (leave blank to auto-detect)",
    )
    parser.add_argument(
        "--remote",
        "-r",
        default=None,
        help="Remote host via SSH (e.g. hexastorm@pihome.home)",
    )
    parser.add_argument(
        "--micropython-dir",
        default=None,
        help="Path to MicroPython root or ports/esp32 directory (overrides MICROPYTHON_DIR)",
    )
    parser.add_argument(
        "--idf-path",
        default=None,
        help="Path to ESP-IDF directory containing export.sh (overrides IDF_PATH)",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Compile firmware inside the reproducible Docker builder container",
    )
    parser.add_argument(
        "--rebuild-image",
        action="store_true",
        help="Force rebuilding the Docker firmware builder image before building",
    )
    parser.add_argument(
        "--docker-image",
        default=DEFAULT_DOCKER_IMAGE,
        help="Docker builder image tag to use with --docker",
    )
    parser.add_argument(
        "--fpga-bit",
        default=None,
        help="Path to prebuilt FPGA .bit file to bundle instead of synthesizing HDL",
    )
    parser.add_argument(
        "--rebuild-fpga",
        action="store_true",
        help="Force resynthesizing the iCE40UP5K FPGA bitstream",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build firmware binaries without checking for or flashing an ESP32",
    )
    args = parser.parse_args()

    paths = resolve_build_paths(
        micropython_dir=args.micropython_dir,
        idf_path=args.idf_path,
        use_docker=args.docker,
    )

    _validate_submodule_deps(paths["repo_root"])

    if not os.path.isfile(os.path.join(paths["board_dir"], "mpconfigboard.cmake")):
        logger.error(f"Out-of-tree board directory incomplete: {paths['board_dir']}")
        sys.exit(1)

    # Pre-flight check: ensure device is ready to be flashed BEFORE building.
    active_port = None
    if not args.build_only:
        logger.info("--- Pre-flight Device Check ---")
        if args.remote:
            active_port = verify_and_prepare_remote_device(args.remote, args.port)
        else:
            active_port = verify_and_prepare_device(args.port)

    if args.docker:
        ensure_docker_image(
            paths["repo_root"],
            image_name=args.docker_image,
            rebuild=args.rebuild_image,
        )
        host_uid = os.getuid() if sys.platform.startswith("linux") else None
        host_gid = os.getgid() if sys.platform.startswith("linux") else None
        docker_cmd = build_docker_run_command(
            paths,
            image_name=args.docker_image,
            host_uid=host_uid,
            host_gid=host_gid,
            fpga_bit=args.fpga_bit,
            rebuild_fpga=args.rebuild_fpga,
        )
        logger.info("--- Building Firmware (Docker) ---")
        subprocess.run(docker_cmd, check=True)
    else:
        if not os.path.exists(paths["esp32_port_dir"]):
            logger.error(
                f"MicroPython ESP32 port directory not found at {paths['esp32_port_dir']}. "
                "Set MICROPYTHON_DIR, pass --micropython-dir, or use --docker."
            )
            sys.exit(1)

        if not os.path.exists(paths["idf_export_path"]):
            logger.error(
                f"ESP-IDF export.sh not found at {paths['idf_export_path']}. "
                "Set IDF_PATH, pass --idf-path, or use --docker."
            )
            sys.exit(1)

        prepare_native_fpga_bitstream(
            paths["repo_root"],
            fpga_bit=args.fpga_bit,
            rebuild_fpga=args.rebuild_fpga,
        )

        make_cmd = [
            "make",
            f"-j{os.cpu_count() or 4}",
            f"BOARD={paths['board_name']}",
            f"BOARD_DIR={paths['board_dir']}",
            f"FROZEN_MANIFEST={paths['manifest_path']}",
            f"USER_C_MODULES={paths['cmake_path']}",
        ]
        quoted_make = " ".join(shlex.quote(part) for part in make_cmd)
        build_cmd = f"source {shlex.quote(paths['idf_export_path'])} && {quoted_make}"

        logger.info("--- Building Firmware (Native) ---")
        # executable='/bin/bash' is required because 'source' is a bash built-in
        subprocess.run(
            build_cmd,
            shell=True,
            cwd=paths["esp32_port_dir"],
            executable="/bin/bash",
            check=True,
        )

    local_build_dir = paths["local_build_dir"]

    if args.build_only:
        logger.info(f"Build complete (--build-only). Binaries in: {local_build_dir}")
        return

    assert active_port is not None
    if args.remote:
        logger.info(f"--- Transferring Binaries to Remote Target ({args.remote}) ---")
        transfer_binaries_remote(args.remote, local_build_dir)

        remote_flash_cmd = (
            f"export PATH=$HOME/.local/bin:$PATH && "
            f"esptool --chip esp32s3 -p {shlex.quote(active_port)} -b 460800 --before=default_reset "
            f"--after=hard_reset write_flash --flash_mode dio --flash_freq 80m "
            f"--flash_size 32MB 0x0 /tmp/esp32_build/bootloader.bin "
            f"0x10000 /tmp/esp32_build/micropython.bin "
            f"0x8000 /tmp/esp32_build/partition-table.bin "
            f"0xd000 /tmp/esp32_build/ota_data_initial.bin"
        )

        logger.info(f"--- Flashing Firmware Remotely on {args.remote} ---")
        run_remote_ssh(args.remote, remote_flash_cmd, check=True, capture_output=False)
    else:
        logger.info("--- Flashing Firmware ---")
        check_port_availability(active_port)
        subprocess.run(
            build_local_flash_command(active_port, local_build_dir),
            check=True,
        )

    logger.info("Firmware flashed successfully!")


if __name__ == "__main__":
    main()
