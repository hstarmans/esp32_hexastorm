# Microdot webserver for laser scanner control

The project implements a Microdot webserver on an ESP32 which enables remote control of an UP5K FPGA programmed with the 
[Hexastorm](https://github.com/hstarmans/hexastorm) Amaranth HDL toolchain.  
It enables toggling the laser, prism motor, movement of the laserhead and execution of a print job.
The webserver is on default secured by a password which is "hex".  
Screenshot of the webserver after login is given below.  
<img src="images/webserver.png" align="center" height="300"/>  
Detailed installation information is available in [developer.md](developer.md).