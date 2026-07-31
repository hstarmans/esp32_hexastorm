"""CPython-compatible shim for MicroPython's ``deflate`` module.

MicroPython provides ``deflate.DeflateIO(stream, deflate.ZLIB)`` for
streaming ZLIB decompression.  CPython doesn't have this module, so we
emulate it with ``zlib.decompressobj``.

Usage (in base.py)::

    import deflate

    with deflate.DeflateIO(f, deflate.ZLIB) as d:
        data = d.read(n)
"""

import zlib

# Match MicroPython's ``deflate.ZLIB`` constant
ZLIB = 2


class DeflateIO:
    """Streaming ZLIB decompressor that wraps a binary file object.

    Provides a file-like ``read(n)`` interface identical to
    MicroPython's ``deflate.DeflateIO``.
    """

    def __init__(self, stream, fmt=ZLIB):
        if fmt != ZLIB:
            raise ValueError(f"Only ZLIB format is supported, got {fmt}")
        self._stream = stream
        # wbits=15 for ZLIB header
        self._decompressor = zlib.decompressobj(15)
        self._buffer = b""

    def read(self, n=-1):
        """Read *n* decompressed bytes.  Reads compressed chunks from the
        underlying stream as needed."""
        if n < 0:
            # Read everything
            chunks = [self._buffer]
            while True:
                raw = self._stream.read(4096)
                if not raw:
                    break
                chunks.append(self._decompressor.decompress(raw))
            chunks.append(self._decompressor.flush())
            self._buffer = b""
            return b"".join(chunks)

        # Read exactly n bytes
        while len(self._buffer) < n:
            raw = self._stream.read(4096)
            if not raw:
                # End of compressed stream — flush remaining
                self._buffer += self._decompressor.flush()
                break
            self._buffer += self._decompressor.decompress(raw)

        result = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
