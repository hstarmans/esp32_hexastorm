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

- maak een install script  (check)
- connectie internet        (check)
- installeer webserver, check of die draait  (check)
- check of je bestand kan uploaden  (werkt op esp32s3)
- Reduceer geheugen via async call (check)
- verander license  (check)
- login via een REST methode (check)
- stuur commando naar websocket (check)
- upload file (check)
- fixture in fixture (check)
- remove file (check)
- sla dit op als test code (check)
- bouw een binary met ota (check)
- voer een ota update uit
     # check release date en of deze nieuwer is
     # cleanup deze repo
     # kijk of bestanden persistent zin
     --> sla bestand op via github LFS
     --> creeer een github access token
     --> download via github access token
     --> voer update uit en kijk welke bestanden blijven


- bouw een binary met unumpy en de stepper library
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
