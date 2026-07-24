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


def main():
    parser = argparse.ArgumentParser(description="Build and flash ESP32 firmware")
    parser.add_argument(
        "--port", "-p", default=None, help="Serial port of the ESP32 (leave blank to auto-detect)"
    )
    args = parser.parse_args()

    # Pre-flight check: ensure device is ready to be flashed BEFORE building.
    logger.info("--- Pre-flight Device Check ---")
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
        f"USER_C_MODULES={CMAKE_PATH}"
    ]
    build_cmd = f"source {IDF_EXPORT_PATH} && {' '.join(make_cmd)}"

    logger.info("--- Building Firmware ---")
    # executable='/bin/bash' is required because 'source' is a bash built-in
    subprocess.run(build_cmd, shell=True, cwd=ESP32_PORT_DIR, executable='/bin/bash', check=True)

    # Flash command
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
    subprocess.run(flash_cmd, shell=True, cwd=ESP32_PORT_DIR, executable='/bin/bash', check=True)

    logger.info("Firmware flashed successfully!")


if __name__ == "__main__":
    main()
