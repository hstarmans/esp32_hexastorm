"""
ulab.py - Compatibility stub for host dev tools (Ruff, Pyright, pytest).
On ESP32 hardware, MicroPython's built-in C module 'ulab' takes precedence.
This file is frozen to the hexastorm-micropython-firmware.
"""
try:
    import numpy
except ImportError:
    numpy = None