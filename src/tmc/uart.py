import time
import struct
import logging
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
    _IFCNT = 0x02

    def __init__(
        self,
        mtr_id,
        uart_dct,
        communication_pause=None,
    ):
        """
        Initialize UART communication with a TMC driver.

        Args:
            mtr_id (int): Motor/driver address (0-3 for TMC2209).
            uart_dct: dict: UART configuration dictionary
            communication_pause (float, optional): Minimum pause (in seconds) between UART operations.
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

    # -------------------- internals --------------------

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
        Read exactly n bytes from UART, with timeout.

        Args:
            n (int): Number of bytes to read.
            timeout_ms (int, optional): Timeout in milliseconds.
        Returns:
            bytes: Data read from UART.
        """
        buf = bytearray()
        ticks_ms = getattr(time, "ticks_ms", None)
        ticks_diff = getattr(time, "ticks_diff", None)

        if ticks_ms and ticks_diff:
            start = ticks_ms()
            while len(buf) < n:
                chunk = self.ser.read(n - len(buf))
                if chunk:
                    buf.extend(chunk)
                    continue
                if timeout_ms is None:
                    ser_timeout = getattr(self.ser, "timeout", None)
                    timeout_ms = 2 * (ser_timeout if ser_timeout else 20)
                if ticks_diff(ticks_ms(), start) >= timeout_ms:
                    break
        else:
            start = time.time()
            while len(buf) < n:
                chunk = self.ser.read(n - len(buf))
                if chunk:
                    buf.extend(chunk)
                    continue
                if timeout_ms is None:
                    timeout_ms = 40
                if (time.time() - start) * 1000.0 >= timeout_ms:
                    break

        return bytes(buf)

    # -------------------- public API --------------------

    def read_reg(self, reg):
        """
        Read a 32-bit register from the TMC driver.

        Args:
            reg (int): Register address.
        Returns:
            bytes: 4 data bytes from the register.
        Raises:
            ConnectionFail: If communication fails.
        """
        self._r_frame[1] = self.mtr_id & 0xFF
        self._r_frame[2] = reg & 0x7F
        self._r_frame[3] = self._crc8_atm(self._r_frame[:3])

        written = self.ser.write(self._r_frame)
        if written != self._READ_FRAME_LEN:
            logger.warning("TMC: short write on read_reg (wrote %s)", written)
            raise ConnectionFail()

        time.sleep(self.communication_pause)

        reply = self._read_exact(self._REPLY_LEN)
        if len(reply) < self._REPLY_LEN:
            raise ConnectionFail()

        if reply is None:
            raise ConnectionFail()
        return reply[7:11]

    def read_int(self, reg, retries=10):
        """
        Read a signed 32-bit register value.

        Args:
            reg (int): Register address.
            retries (int): Number of retries on failure.
        Returns:
            int: Signed 32-bit value.
        Raises:
            ConnectionFail: If all retries fail.
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
        logger.debug(
            "TMC: after %d tries no valid answer. Is the stepper PSU on?",
            retries,
        )
        if last_err is None:
            raise ConnectionFail()
        raise last_err

    def read_u32(self, reg, retries=10):
        """
        Read an unsigned 32-bit register value.

        Args:
            reg (int): Register address.
            retries (int): Number of retries on failure.
        Returns:
            int: Unsigned 32-bit value.
        Raises:
            ConnectionFail: If all retries fail.
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
        if last_err is None:
            raise ConnectionFail()
        raise last_err

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

        IFCNT is a counter inside the chip that increments every time
        a valid SPI (or UART) write transaction is received.

        Args:
            reg (int): Register address.
            val (int): 32-bit value to write.
        Returns:
            bool: True if write was successful, False otherwise.
        """
        try:
            before = self.read_int(self._IFCNT) & 0xFF
            self.write_reg(reg, val)
            after = self.read_int(self._IFCNT) & 0xFF
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
        """Update only bits in `mask` to match `value`."""
        cur = self.read_u32(reg)
        new_val = (cur & ~mask) | (value & mask)
        self.write_reg(reg, new_val)

    def probe(self):
        """
        Quick check if the TMC responds over UART.
        Returns a dict with IFCNT and GSTAT, or False on failure.
        """
        GSTAT = 0x01
        try:
            ifcnt = self.read_u32(self._IFCNT)
            gstat = self.read_u32(GSTAT)
            return {"ifcnt": ifcnt, "gstat": gstat}
        except ConnectionFail:
            return False

    # -------------------- bit helpers --------------------

    @staticmethod
    def set_bit(value, mask):
        """Set bits in value according to mask."""
        return value | mask

    @staticmethod
    def clear_bit(value, mask):
        """Clear bits in value according to mask."""
        return value & (~mask)

    def flushSerialBuffer(self):
        """Drain any pending bytes from UART RX."""
        try:
            while True:
                data = self.ser.read()
                if not data:
                    break
        except Exception:
            pass
