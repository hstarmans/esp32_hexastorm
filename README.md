# Webserver to control a laser scanner

Provides a Microdot webserver to control a laser scanner built using the [Hexastorm](https://github.com/hstarmans/hexastorm)
library with an ESP32.

## TODO:

- voer een ota update uit
     # check release date en of deze nieuwer is
     # cleanup deze repo
     # kijk of bestanden persistent zin
     --> sla bestand op via github LFS  (check)
     --> creeer een github access token  (check)
     --> download via github access token  (check)
     --> voer update uit en kijk welke bestanden blijven  (task is moved back)
- bouw binary met unumpy en de stepper library (check)
- maak een binary met icestorm (check)
- maak install script (check)
- maak blinkly binary (check)
- ontvang de status via sse methode  (check)
- voeg mogelijkheid toe om unit testen uit te voeren op de esp32 (check)
- huidige idee is om hexastorm esp32 compatable te maken en de testen te draaien op de ESP32
- development cycle is micropython linux --> micropython esp 32 (check)
- voeg mogelijkheid toe fpga binary te uploaden (dat deze folder selecteert)
- voeg commando toe om fpga te flashen
- herimplementeer de testen

- flash met hexastorm
- copy binary to board
  ** stap 1 maak hello world binary
  ** stap 2 kunnen we keyword naar build sturen
  ** stap 3 copy binary to fpga




- flash fpga
- bewegen stepper motoren
- commando's laser kop
- uitvoeren print ** klaar





Test on existing hardware
   - add link with existing code
   - test if speed is sufficient
   - modify boot procedures

Clean up code
  - split of debug modes
  


Keep repo as is, add the requirements
Keep it minimal, there is enough to do.
We push untill the end of march, if it is not finished by then.
Let's focus on hardware.
