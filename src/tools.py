"""
Utilities for Hexastorm FPGA Control

This module provides quick access to often-used tools for interacting with the
Hexastorm FPGA and managing the control environment.

Attributes:
    host (ESP32HostSync): An instance used to interact with the FPGA.
        It is configured for synchronous (blocking) operations.
    reload (function): A reference to the hot_reload function, used to
        reload control modules without restarting the environment.
    test (function): A reference to the run_test function, used to execute
        a standard Hexastorm test suite e.g. ("StaticTest","test_memfull")
"""

from hexastorm.fpga_host.micropython import ESP32HostSync
from control.reloading import hot_reload
from control.tests.test_hexastorm import run_test

host = ESP32HostSync(sync=True)
reload = hot_reload
test = run_test
