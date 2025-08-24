import time
import struct
import logging

from .reg import GSTAT, IFCNT

from machine import UART

logger = logging.getLogger(__name__)


class ConnectionFail(Exception):
    """Raised when the TMC does not respond with a valid frame after retries."""


class TMC_UART:
    """
    Minimal UART helper for TMC drivers (e.g., TMC2209).
    Provides register read/write with CRC8-ATM integrity checks.
    """

    # Protocol constants
    _SYNC = 0x55  # sync byte used in r/w frames
    _READ_FRAME_LEN = 4
    _WRITE_FRAME_LEN = 8
    _REPLY_LEN = 12  # typical reply length from TMC (sync..crc, 12 bytes)

    def __init__(self, mtr_id, uart_dct, communication_pause=None):
        """
        Initialize UART communication with a TMC driver.

        Args:
            mtr_id (int): Motor/driver address (0-3 for TMC2209).
            uart_dct (dict): UART configuration dict with 'id', 'ctor', 'init'.
            communication_pause (float, optional): Minimum pause (s) between UART ops.
        """
        self.mtr_id = mtr_id
        self.ser = UART(uart_dct["id"], **uart_dct["ctor"])
        self.ser.init(**uart_dct["init"])

        if communication_pause is not None:
            self.communication_pause = communication_pause
        else:
            self.communication_pause = max(
                1.0 / uart_dct["ctor"].get("baudrate", 115200) * 500, 0.0005
            )

        self._r_frame = bytearray([self._SYNC, 0x00, 0x00, 0x00])
        self._w_frame = bytearray(
            [self._SYNC, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        )

    def __del__(self):
        """Deinitialize UART on object deletion."""
        self.ser.close()

    # -------------------- internals (MicroPython only) --------------------

    @staticmethod
    def _crc8_atm(data, initial=0x00):
        """Calculate CRC8-ATM for a given data buffer."""
        crc = initial & 0xFF
        for byte in data:
            b = byte
            for _ in range(8):
                if ((crc >> 7) ^ (b & 0x01)) & 0x01:
                    crc = ((crc << 1) ^ 0x07) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
                b >>= 1
        return crc

    def _read_exact(self, n, timeout_ms=None):
        """
        Read exactly n bytes from UART or return fewer on timeout.

        Args:
            n (int): Number of bytes requested.
            timeout_ms (int, optional): Max time to wait for the whole read.
        Returns:
            bytes: Up to n bytes read before the timeout.
        """
        buf = bytearray()

        # default timeout: 2x UART timeout or 40ms
        if timeout_ms is None:
            ser_timeout = getattr(self.ser, "timeout", None)
            timeout_ms = int(2 * (ser_timeout if ser_timeout else 20))

        start = time.ticks_ms()
        while len(buf) < n:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf.extend(chunk)
            if time.ticks_diff(time.ticks_ms(), start) >= timeout_ms:
                break

        return bytes(buf)

    def _read_reply(
        self, expect_addr=None, expect_reg=None, min_len=12, timeout_ms=None
    ):
        """
        Tolerant reply reader.

        Behavior:
          - Waits for incoming bytes (uses UART.any()).
          - Accumulates a buffer and finds the last 0x55 that still leaves ≥11 bytes after it.
          - Performs optional CRC and echo checks (lenient: warn only).

        Args:
            expect_addr (int|None): Optional expected slave address (echo).
            expect_reg (int|None): Optional expected register (echo).
            min_len (int): Minimum frame length to return (default 12).
            timeout_ms (int|None): Per-frame timeout.

        Returns:
            bytes: A frame (length >= min_len) starting at the chosen SYNC.

        Raises:
            ConnectionFail: If no plausible frame is found before timeout.
        """
        SYNC = self._SYNC

        # default per-frame timeout
        if timeout_ms is None:
            ser_timeout = getattr(self.ser, "timeout", None)
            timeout_ms = int(3 * (ser_timeout if ser_timeout else 20))

        start = time.ticks_ms()
        buf = bytearray()

        while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
            # wait until at least some bytes are available
            if not self.ser.any():
                time.sleep(0.0005)
                continue

            # read whatever is available
            chunk = self.ser.read()
            if chunk:
                buf.extend(chunk)

                # try to find a plausible frame within buf
                last_sync = -1
                for i in range(len(buf)):
                    if buf[i] == SYNC and (i + 11) < len(buf):
                        last_sync = i

                if last_sync >= 0:
                    # In practice TMC replies are 12 bytes. Keep only min_len from sync if available.
                    end = min(last_sync + min_len, len(buf))
                    frame = bytes(buf[last_sync:end])

                    # Optional: CRC check over first 11 bytes (warn only)
                    if len(frame) >= 12:
                        calc_crc = self._crc8_atm(frame[:11])
                        rx_crc = frame[11]
                        if calc_crc != rx_crc:
                            logger.debug(
                                "TMC: CRC mismatch (got 0x%02X, want 0x%02X) — continuing (lenient).",
                                rx_crc,
                                calc_crc,
                            )

                    # Optional: addr/reg echo checks (lenient)
                    if expect_addr is not None and len(frame) > 2:
                        if frame[1] != (expect_addr & 0xFF):
                            logger.debug(
                                "TMC: addr echo mismatch (0x%02X vs 0x%02X) — continuing.",
                                frame[1],
                                expect_addr & 0xFF,
                            )
                    if expect_reg is not None and len(frame) > 3:
                        reg_echo = frame[2] & 0x7F
                        if reg_echo != (expect_reg & 0x7F):
                            logger.debug(
                                "TMC: reg echo mismatch (0x%02X vs 0x%02X) — continuing.",
                                reg_echo,
                                expect_reg & 0x7F,
                            )

                    return frame

            # not enough yet; small sleep then loop
            time.sleep(0.0005)

        raise ConnectionFail()

    # -------------------- public API --------------------

    def read_reg(self, reg):
        """
        Read four raw data bytes from a TMC register.

        Args:
            reg (int): Register address (0x00..0x7F).
        Returns:
            bytes: 4 bytes of register payload (big-endian order).
        Raises:
            ConnectionFail: If the read frame or reply fails.
        """
        # build+send 4-byte read frame
        self._r_frame[1] = self.mtr_id & 0xFF
        self._r_frame[2] = reg & 0x7F
        self._r_frame[3] = self._crc8_atm(self._r_frame[:3])

        written = self.ser.write(self._r_frame)
        if written != self._READ_FRAME_LEN:
            logger.warning("TMC: short write on read_reg (wrote %s)", written)
            raise ConnectionFail()

        time.sleep(self.communication_pause)

        # read one tolerant reply
        reply = self._read_reply(
            expect_addr=self.mtr_id, expect_reg=reg, min_len=self._REPLY_LEN
        )

        # keep your established payload slice
        if len(reply) < 11:
            raise ConnectionFail()
        return reply[7:11]

    def read_int(self, reg, retries=10):
        """
        Read a signed 32-bit value from a register with retry.

        Args:
            reg (int): Register address.
            retries (int): Max attempts before failing.
        Returns:
            int: Signed 32-bit value.
        Raises:
            ConnectionFail: If no valid 4-byte payload is received after retries.
        """
        last_err = None
        for _ in range(retries):
            try:
                raw = self.read_reg(reg)
                if len(raw) == 4:
                    return struct.unpack(">i", raw)[0]
                logger.debug("TMC: expected 4 data bytes, got %d", len(raw))
            except ConnectionFail as e:
                last_err = e
            time.sleep(self.communication_pause)
        logger.debug("TMC: no valid answer after %d tries (PSU on?)", retries)
        raise last_err or ConnectionFail()

    def read_u32(self, reg, retries=10):
        """
        Read an unsigned 32-bit value from a register with retry.

        Args:
            reg (int): Register address.
            retries (int): Max attempts before failing.
        Returns:
            int: Unsigned 32-bit value.
        Raises:
            ConnectionFail: If no valid 4-byte payload is received after retries.
        """
        last_err = None
        for _ in range(retries):
            try:
                raw = self.read_reg(reg)
                if len(raw) == 4:
                    return struct.unpack(">I", raw)[0]
                logger.debug("TMC: expected 4 data bytes, got %d", len(raw))
            except ConnectionFail as e:
                last_err = e
            time.sleep(self.communication_pause)
        raise last_err or ConnectionFail()

    def write_reg(self, reg, val):
        """
        Write a 32-bit value to a register.

        Args:
            reg (int): Register address.
            val (int): 32-bit value to write.
        Raises:
            ConnectionFail: If communication fails.
        """
        self._w_frame[1] = self.mtr_id & 0xFF
        self._w_frame[2] = (reg & 0x7F) | 0x80

        self._w_frame[3] = (val >> 24) & 0xFF
        self._w_frame[4] = (val >> 16) & 0xFF
        self._w_frame[5] = (val >> 8) & 0xFF
        self._w_frame[6] = val & 0xFF

        self._w_frame[7] = self._crc8_atm(self._w_frame[:7])

        written = self.ser.write(self._w_frame)
        if written != self._WRITE_FRAME_LEN:
            logger.warning("TMC: short write on write_reg (wrote %s)", written)
            raise ConnectionFail()

        time.sleep(self.communication_pause)

    def write_reg_check(self, reg, val):
        """
        Write a register and verify the update via IFCNT.

        IFCNT increments on each valid UART/SPI write.

        Args:
            reg (int): Register address.
            val (int): 32-bit value to write.
        Returns:
            bool: True if IFCNT advanced, False otherwise.
        """
        try:
            before = self.read_int(IFCNT) & 0xFF
            self.write_reg(reg, val)
            after = self.read_int(IFCNT) & 0xFF
        except ConnectionFail:
            logger.info("TMC: write/ifcnt check failed (no response)")
            return False

        progressed = (after - before) & 0xFF
        if progressed <= 0:
            logger.info(
                "TMC: write not successful (IFCNT %d -> %d)", before, after
            )
            logger.info("reg: 0x%02X val: 0x%08X", reg, val & 0xFFFFFFFF)
            return False
        return True

    def read_modify_write(self, reg, mask, value):
        """Read-modify-write helper: only bits in `mask` are updated to match `value`."""
        cur = self.read_u32(reg)
        new_val = (cur & ~mask) | (value & mask)
        self.write_reg_check(reg, new_val)

    def probe(self):
        """
        Quick UART liveness check.

        Returns:
            dict|bool: {'ifcnt': int, 'gstat': int} on success; False on failure.
        """
        try:
            ifcnt = self.read_u32(IFCNT)
            gstat = self.read_u32(GSTAT)
            return {"ifcnt": ifcnt, "gstat": gstat}
        except ConnectionFail:
            return False

    # -------------------- bit helpers --------------------

    @staticmethod
    def set_bit(value, mask):
        """Return value with bits in mask set."""
        return value | mask

    @staticmethod
    def clear_bit(value, mask):
        """Return value with bits in mask cleared."""
        return value & (~mask)

    def flushSerialBuffer(self):
        """Drain any pending bytes from UART RX; ignore errors."""
        try:
            while True:
                data = self.ser.read()
                if not data:
                    break
        except Exception:
            pass
