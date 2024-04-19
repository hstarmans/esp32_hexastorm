# Webserver to control a laser scanner

Provides a Microdot webserver to control a laser scanner built using the [Hexastorm](https://github.com/hstarmans/hexastorm)
library with an ESP32.


## Render template consumes a lot of memory

Proposal
 - je moet html uit meerdere documenten kunnen maken
   https://stackoverflow.com/questions/34238131/how-to-separate-html-text-file-into-multiple-files
 - de sub elementen laat je renderen
   via render a small template
 



## TODO:

- voer een ota update uit
     # check release date en of deze nieuwer is
     # cleanup deze repo
     # kijk of bestanden persistent zin
     --> sla bestand op via github LFS  (check)
     --> creeer een github access token  (check)
     --> download via github access token  (check)
     --> voer update uit en kijk welke bestanden blijven
- bouw binary met unumpy en de stepper library (check)
- maak een binary met icestorm (check)
- maak install script (check)
- maak blinkly binary (check)
- ontvang de status via sse methode
- voeg mogelijkheid toe fpga binary te uploaden
- voeg commando toe om fpga te flashen
- voeg commandos toe aan hexastorm flow
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
