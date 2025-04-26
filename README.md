# Microdot Webserver for Remote Laser Scanner Control

This project provides a Microdot webserver running on an ESP32 microcontroller, enabling remote control of a laser scanner system. The core of the scanner is a Lattice UP5K FPGA, programmed using the [Hexastorm](https://github.com/hstarmans/hexastorm) Amaranth HDL toolchain.

The web interface allows users to remotely manage key functionalities of the laser scanner, including:

* **Laser Control:** Toggle the laser on or off.
* **Prism Motor Control:** Control the rotation of the prism motor.
* **Laser Head Movement:** Control the positioning and movement of the laser head.
* **Print Job Execution:** Initiate and manage print jobs.

For security, the webserver is protected by a default password: **`hex`**.

A visual overview of the webserver interface after successful login is shown below:

<img src="images/webserver.png" align="center" height="300"/>

Comprehensive installation and setup instructions for developers can be found in the [developer.md](developer.md) file.