import os
import sys
import time
import subprocess
import argparse
import logging
import serial
import serial.tools.list_ports

# Add src directory to path to import ESP32Controller and log_setup
sys.path.append(os.path.dirname(__file__))
from hexastorm.esp32_controller import ESP32Controller
from hexastorm.log_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# --- Configuration Paths ---
HOME_DIR = os.path.expanduser("~")
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ESP32_PORT_DIR = os.path.join(HOME_DIR, "Documents", "Python", "micropython", "ports", "esp32")
IDF_EXPORT_PATH = os.path.join(HOME_DIR, "Documents", "Python", "esp-idf", "export.sh")
MANIFEST_PATH = os.path.join(SRC_DIR, "manifest.py")
CMAKE_PATH = os.path.join(SRC_DIR, "micropython.cmake")
# -------------------------

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
        return port_arg, None # Port exists but maybe not USB, or we can't get PID
    
    # Auto-detect
    for p in ports:
        if p.vid == 0x303A: # Espressif VID
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
    except Exception:
        pass
    return None, None


def check_port_availability(port):
    """
    Verifies that the serial port is not currently in use by another application.
    Exits with a clear error message if the port is occupied.
    """
    proc_pid, proc_name = find_process_using_port(port)
    if proc_pid:
        logger.error(f"Serial port {port} is currently in use by '{proc_name}' (PID {proc_pid}).")
        logger.error("Please close Thonny or any active serial monitor/terminal connected to the board and try again.")
        sys.exit(1)

    try:
        s = serial.Serial()
        s.port = port
        s.exclusive = True
        s.open()
        s.close()
    except (serial.SerialException, OSError) as e:
        logger.error(f"Cannot open serial port {port}: {e}")
        logger.error("The port appears to be locked or in use by another application (e.g. Thonny).")
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
            logger.info("Successfully connected to MicroPython REPL. Rebooting device to bootloader...")
            try:
                ctrl.exec_no_wait("import machine; machine.bootloader()")
            except Exception:
                pass
            ctrl.close()
        except serial.SerialException as e:
            logger.error(f"Cannot access serial port {port}: {e}")
            logger.error("The port is currently in use by another application (e.g., Thonny).")
            logger.error("Please close Thonny or any serial monitor connected to the device and try again.")
            sys.exit(1)
        except Exception as e:
            logger.error("Failed to get a prompt from the device. It is unresponsive!")
            logger.warning("\n*** RECOVERY TRICK INITIATED ***")
            logger.warning("Attempting automatic hardware reset via DTR/RTS...")
            
            try:
                with serial.Serial(port, 115200) as s:
                    s.setDTR(False)
                    s.setRTS(True)
                    time.sleep(0.1)
                    s.setDTR(True)
                    s.setRTS(False)
                    time.sleep(0.1)
                    s.setDTR(False)
                    s.setRTS(False)
            except Exception:
                pass
                
            time.sleep(0.5)
            if os.path.exists(port):
                # The port didn't drop, meaning the hardware reset failed (old TinyUSB firmware)
                logger.error("Automatic hardware reset failed (expected on the first run).")
                logger.warning("Please press the physical RESET button on your board NOW!")
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
                except Exception:
                    pass
                time.sleep(0.05)
                
            if not caught:
                logger.error("Failed to catch the board. Please try running build again and resetting faster.")
                sys.exit(1)
            
        logger.info("Waiting for the board to enter bootloader mode (PID 0x1001)...")
        timeout = 10
        start_time = time.time()
        while time.time() - start_time < timeout:
            new_port, new_pid = find_espressif_device(port_arg)
            if new_pid == 0x1001:
                logger.info(f"Board successfully entered bootloader mode on {new_port}!")
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
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", remote_host, command_str]
    return subprocess.run(ssh_cmd, check=check, capture_output=capture_output, text=True)


def run_remote_python(remote_host, script_content):
    """Executes a Python script on the remote host via SSH stdin."""
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", remote_host, "python3 -"]
    return subprocess.run(ssh_cmd, input=script_content, text=True, capture_output=True)



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
            logger.error(f"Failed to prepare remote device on {remote_host}: {res.stderr or res.stdout}")
        sys.exit(1)

    for line in res.stdout.splitlines():
        if line.startswith("READY:"):
            remote_port = line.split(":", 1)[1].strip()
            logger.info(f"Remote device on {remote_host} ready on port {remote_port}")
            return remote_port

    logger.error(f"Unexpected response from remote pre-flight check: {res.stdout}")
    sys.exit(1)



def transfer_binaries_remote(remote_host, local_build_dir, remote_dir="/tmp/esp32_build"):
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
    scp_cmd = ["scp", "-q"] + local_files + [f"{remote_host}:{remote_dir}/"]
    res = subprocess.run(scp_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"Failed to transfer binaries over scp: {res.stderr}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build and flash ESP32 firmware")
    parser.add_argument(
        "--port", "-p", default=None, help="Serial port of the ESP32 (leave blank to auto-detect)"
    )
    parser.add_argument(
        "--remote", "-r", default=None, help="Remote host via SSH (e.g. hexastorm@pihome.home)"
    )
    args = parser.parse_args()

    # Pre-flight check: ensure device is ready to be flashed BEFORE building.
    logger.info("--- Pre-flight Device Check ---")
    if args.remote:
        active_port = verify_and_prepare_remote_device(args.remote, args.port)
    else:
        active_port = verify_and_prepare_device(args.port)

    # Verify the micropython port directory exists
    if not os.path.exists(ESP32_PORT_DIR):
        logger.error(f"MicroPython ESP32 port directory not found at {ESP32_PORT_DIR}")
        sys.exit(1)

    if not os.path.exists(IDF_EXPORT_PATH):
        logger.error(f"ESP-IDF export.sh not found at {IDF_EXPORT_PATH}")
        sys.exit(1)

    # Build command
    make_cmd = [
        "make",
        f"-j $(nproc)",
        "BOARD=ESP32_GENERIC_S3",
        "BOARD_VARIANT=SPIRAM_OCT",
        f"FROZEN_MANIFEST={MANIFEST_PATH}",
        f"USER_C_MODULES={CMAKE_PATH}",
    ]
    build_cmd = f"source {IDF_EXPORT_PATH} && {' '.join(make_cmd)}"

    logger.info("--- Building Firmware ---")
    # executable='/bin/bash' is required because 'source' is a bash built-in
    subprocess.run(build_cmd, shell=True, cwd=ESP32_PORT_DIR, executable="/bin/bash", check=True)

    local_build_dir = os.path.join(ESP32_PORT_DIR, "build-ESP32_GENERIC_S3-SPIRAM_OCT")

    if args.remote:
        logger.info(f"--- Transferring Binaries to Remote Target ({args.remote}) ---")
        transfer_binaries_remote(args.remote, local_build_dir)

        remote_flash_cmd = (
            f"export PATH=$HOME/.local/bin:$PATH && "
            f"esptool --chip esp32s3 -p {active_port} -b 460800 --before=default_reset "
            f"--after=hard_reset write_flash --flash_mode dio --flash_freq 80m "
            f"--flash_size 32MB 0x0 /tmp/esp32_build/bootloader.bin "
            f"0x10000 /tmp/esp32_build/micropython.bin "
            f"0x8000 /tmp/esp32_build/partition-table.bin "
            f"0xd000 /tmp/esp32_build/ota_data_initial.bin"
        )

        logger.info(f"--- Flashing Firmware Remotely on {args.remote} ---")
        run_remote_ssh(args.remote, remote_flash_cmd, check=True, capture_output=False)
    else:
        # Local flash command
        flash_cmd = (
            f"source {IDF_EXPORT_PATH} && "
            f"esptool.py --chip esp32s3 -p {active_port} -b 460800 --before=default_reset "
            f"--after=watchdog_reset write_flash --flash_mode dio --flash_freq 80m "
            f"--flash_size 32MB 0x0 build-ESP32_GENERIC_S3-SPIRAM_OCT/bootloader/bootloader.bin "
            f"0x10000 build-ESP32_GENERIC_S3-SPIRAM_OCT/micropython.bin "
            f"0x8000 build-ESP32_GENERIC_S3-SPIRAM_OCT/partition_table/partition-table.bin "
            f"0xd000 build-ESP32_GENERIC_S3-SPIRAM_OCT/ota_data_initial.bin"
        )

        logger.info("--- Flashing Firmware ---")
        check_port_availability(active_port)
        subprocess.run(flash_cmd, shell=True, cwd=ESP32_PORT_DIR, executable="/bin/bash", check=True)

    logger.info("Firmware flashed successfully!")


if __name__ == "__main__":
    main()

