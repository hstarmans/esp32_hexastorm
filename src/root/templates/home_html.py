# Autogenerated file
def render(authorized, state, selected='Home'):
    yield """
"""
# Autogenerated file
    def render1(*a, **d):
        yield """<!doctype html>
<html lang=\"en\" data-bs-theme=\"dark\">
  <head>
    <!-- Required meta tags -->
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <meta name=\"description\" content=\"Control interface for laser direct imager\">
    <meta name=\"author\" content=\"Rik Starmans and contributors\">

    <!-- Bootstrap CSS -->
    <link href=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css\" rel=\"stylesheet\" integrity=\"sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH\" crossorigin=\"anonymous\">
    <!-- Bootstrap icons -->
    <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/bootstrap-icons@1.3.0/font/bootstrap-icons.css\">
    <!-- jQuery -->

    <title>Hexastorm</title>
 """
    yield from render1()
    yield """
</head>
<body>
"""
# Autogenerated file
    def render2(*a, **d):
        if authorized==True:
            yield """
<!-- Navbar -->
<nav class=\"navbar bg-dark\" data-bs-theme=\"dark\">
    <!-- Container wrapper -->
    <div class=\"container-fluid\">
        <a href=\"/\" class=\"navbar-brand\">
            <img src=\"static/hexastormlogo.webp\" alt=\"hexastorm\" width=\"25%\">
        </a>
        <!-- Icons -->
        <ul class=\"nav justify-content-end\">
            <li class=\"nav-item me-3 me-lg-0\">
                <a class=\"btn btn-dark\" data-bs-toggle=\"modal\" href=\"#terminalModal\"><i class=\"bi bi-terminal\"></i> Terminal</a>
            </li>
            <li class=\"nav-item me-3 me-lg-0\">
                <a class=\"btn btn-dark\" data-bs-toggle=\"modal\" href=\"#wifiModal\"><i class=\"bi bi-wifi\"></i> Wifi</a>
            </li>
            <li class=\"nav-item me-3 me-lg-0\">
                <a class=\"btn btn-dark\" href=\"/logout\"><i class=\"bi bi-box-arrow-right\"></i> Logout</a>
            </li>
        </ul>
    </div>
    <!-- Container wrapper -->
</nav>
<br>
"""
        yield """



<!-- Select wifi modal -->
<div class=\"modal fade\" id=\"wifiModal\" tabindex=\"-1\" aria-labelledby=\"wifiModalLabel\" aria-hidden=\"true\">
    <div class=\"modal-dialog\">
        <div class=\"modal-content\">
            <div class=\"modal-header\">
                <h1 class=\"modal-title fs-5\" id=\"wifiModalLabel\">Change wifi</h1>
                <button type=\"button\" class=\"btn-close\" data-bs-dismiss=\"modal\" aria-label=\"Close\"></button>
            </div>
            <div class=\"modal-body\">
                <p id=\"wificonnectedtext\"> Connected to wifi not defined.</p>
                <form>
                    <div class=\"mb-3\">
                        <label for=\"selectedwifi\" class=\"form-label\">Available wifis</label>
                        <select id=\"selectedwifi\" class=\"form-select\">
                            """
        for wifi in state['wifi']['available']:
            yield """
                            <option value=\""""
            yield str(wifi)
            yield """\">"""
            yield str(wifi)
            yield """</option>
                            """
        yield """
                        </select>
                        <label for=\"wifipassword\" class=\"form-label\">Wifi password</label>
                        <input name=\"wifipassword\" type=\"text\" class=\"form-control\" id=\"wifipassword\" value=\""""
        yield str(state['wifi']['password'])
        yield """\" placeholder=\""""
        yield str(state['wifi']['password'])
        yield """\">
                    </div>
                </form>
            </div>
            <div class=\"modal-footer\">
                <button type=\"button\" class=\"btn btn-secondary\" data-bs-dismiss=\"modal\">Close</button>
                <button id=\"changewifibutton\" type=\"button\" class=\"btn btn-success\">Submit</button>
            </div>
        </div>
    </div>
</div>


<!-- Launch terminal modal -->
<div class=\"modal fade\" id=\"terminalModal\" tabindex=\"-1\" aria-labelledby=\"terminalModalLabel\" aria-hidden=\"true\">
    <div class=\"modal-dialog\">
        <div class=\"modal-content\">
            <div class=\"modal-header\">
                <h1 class=\"modal-title fs-5\" id=\"terminalModalLabel\">Webrepl</h1>
                <button type=\"button\" class=\"btn-close\" data-bs-dismiss=\"modal\" aria-label=\"Close\"></button>
            </div>
            <div class=\"modal-body\">
                <form>
                    <p>WARNING: webserver disabled & micropython webrepl launched</p>
                </form>
            </div>
            <div class=\"modal-footer\">
                <button type=\"button\" class=\"btn btn-secondary\" data-bs-dismiss=\"modal\">Close</button>
                <button id=\"startwebreplbutton\" type=\"button\" class=\"btn btn-success\">Launch</button>
            </div>
        </div>
    </div>
</div>
"""
    yield from render2()
    yield """
"""
# Autogenerated file
    def render3(*a, **d):
        yield """
"""
        if state['printing']:
            yield """<div id=\"printingstate\" class=\"container justify-content-center\">
"""
        else:
            yield """<div id=\"printingstate\" style=\"display: none\" class=\"container justify-content-center\">
"""
        yield """  <div class=\"row justify-content-center\">
    <div class=\"col-8\">
      <p class=\"text-center\">Printing progress</p>
        <hr />
        <ul class=\"list-unstyled\">
          <li id=\"filename\">Filename is filename</li>
          <li id=\"lines\">Line of lines</li>
          <li id=\"printingtime\">Time elapsed</li>
          <li id=\"exposure\">Exposures per line at laser power in [a.u]</li>
        </ul>
      <div class=\"progress\">
          <div id=\"progressbar\" class=\"progress-bar progress-bar-striped progress-bar-animated bg-success\" role=\"progressbar\" style=\"width: 25%;\" aria-valuenow=\"25\" aria-valuemin=\"0\" aria-valuemax=\"100\">25%</div>
      </div>
      <br>
      <button id=\"pauseprintbutton\" type=\"button\" class=\"btn btn-success\">
          <i class=\"bi bi-pause\"></i>
      </button>
      <button id=\"stopprintbutton\" type=\"button\" class=\"btn btn-success\">
          <i class=\"bi bi-stop\"></i>
      </button>
    </div>
  </div>
</div>

"""
    yield from render3()
    yield """
"""
    if state['printing']:
        yield """
<div id=\"controlstate\" class=\"container justify-content-center\" style=\"display :none\">
"""
    else:
        yield """
<div id=\"controlstate\" class=\"container justify-content-center\">
"""
    yield """
    <div class=\"row justify-content-center\">
        <div class=\"col-3\">
            """
# Autogenerated file
    def render4(*a, **d):
        yield """<div id=\"xymovement\" class=\"d-grid gap-1 col-12 mx-auto\">
  <p class=\"text-center\">X <i class=\"bi bi-arrow-left-right\"></i>  Y<i class=\"bi bi-arrow-down-up\"></i></p>
  <hr style=\"margin-top:-10px\"/>
  <button class=\"btn btn-success\" type=\"button\" id=\"1,0,0\"><i class=\"bi bi-arrow-up\"></i></button>
  <div class=\"btn-group  gap-1\" role=\"group\" aria-label=\"Basic example\">
    <button type=\"button\" class=\"btn btn-success\" id=\"0,-1,0\"><i class=\"bi bi-arrow-left\"></i></button>
    <button type=\"button\" class=\"btn btn-success\" id=\"-1000,-1000,0\"><i class=\"bi bi-house\"></i></button>
    <button type=\"button\" class=\"btn btn-success\" id=\"0,1,0\"><i class=\"bi bi-arrow-right\"></i></button>
  </div>
  <button class=\"btn btn-success\" type=\"button\" id=\"-1,0,0\"><i class=\"bi bi-arrow-down\"></i></button>
</div>
"""
    yield from render4()
    yield """
        </div>
        <div class=\"col-1\">
            """
# Autogenerated file
    def render5(*a, **d):
        yield """<div id=\"zmovement\" class=\"d-grid gap-1 col-12 mx-auto\">
    <p class=\"text-center\">Z</p>
    <hr style=\"margin-top:-10px\"/>
    <button class=\"btn btn-success\" type=\"button\" id=\"0,0,1\"><i class=\"bi bi-arrow-up\"></i></button>
    <button class=\"btn btn-success\" type=\"button\" id=\"-1000,0,0\"><i class=\"bi bi-house\"></i></button>
    <button class=\"btn btn-success\" type=\"button\" id=\"0,0,-1\"><i class=\"bi bi-arrow-down\"></i></button>
</div>
"""
    yield from render5()
    yield """
        </div>
        <div class=\"col-2\">
            """
# Autogenerated file
    def render6(*a, **d):
        yield """<div class=\"d-grid gap-1 col-12 mx-auto\">
    <p class=\"text-center\">Laserhead</p>
    <hr style=\"margin-top:-10px\"/>
    <button id=\"laser\" class=\"btn btn-success\" type=\"button\" id=\"laser\">Turn laser on</button>
    <button id=\"motor\" class=\"btn btn-success\" type=\"button\" id=\"motor\">Turn motor on</button>
    <button id=\"diode\" class=\"btn btn-success\" type=\"button\" data-bs-toggle=\"modal\" data-bs-target=\"#diodemodal\">Test diode</button>
</div>


<!-- Diode test modal -->
<div class=\"modal fade\" id=\"diodemodal\" tabindex=\"-1\" aria-labelledby=\"diodeModal\" aria-hidden=\"true\">
    <div class=\"modal-dialog\">
        <div class=\"modal-content\">
            <div class=\"modal-header\">
                <h1 class=\"modal-title fs-5\" id=\"exampleModalLabel\">Result diode test</h1>
                <button type=\"button\" class=\"btn-close\" data-bs-dismiss=\"modal\" aria-label=\"Close\"></button>
            </div>
            <div class=\"modal-body\">
                <p id=\"testresult\">Diode test not run.</p>
            </div>
            <div class=\"modal-footer\">
                <button type=\"button\" class=\"btn btn-secondary\" data-bs-dismiss=\"modal\">Close</button>
            </div>
        </div>
    </div>
</div>
"""
    yield from render6()
    yield """
        </div>
        <div class=\"col-2\">
            """
# Autogenerated file
    def render7(*a, **d):
        yield """<div class=\"d-grid gap-1 col-12 mx-auto\">
    <p class=\"text-center\">General</p>
    <hr style=\"margin-top:-10px\"/>
    <button class=\"btn btn-success\" type=\"button\" data-bs-toggle=\"modal\" data-bs-target=\"#printjobmodal\">Start printjob</button>
    <button class=\"btn btn-success\" type=\"button\" data-bs-toggle=\"modal\" data-bs-target=\"#uploadfilemodal\">Upload printjob</button>
    <button class=\"btn btn-success\" type=\"button\" data-bs-toggle=\"modal\" data-bs-target=\"#deletefilemodal\">Delete printjob</button>
</div>


<!-- printjob modal -->
<div class=\"modal fade\" id=\"printjobmodal\" tabindex=\"-1\" aria-labelledby=\"printjobModalLabel\" aria-hidden=\"true\">
    <div class=\"modal-dialog\">
        <div class=\"modal-content\">
            <div class=\"modal-header\">
                <h1 class=\"modal-title fs-5\" id=\"exampleModalLabel\">Printjob settings</h1>
                <button type=\"button\" class=\"btn-close\" data-bs-dismiss=\"modal\" aria-label=\"Close\"></button>
            </div>
            <div class=\"modal-body\">
                <form>
                    <div class=\"mb-3\">
                        <label for=\"printjobfilename\" class=\"form-label\">Filename</label>
                        <select id=\"printjobfilename\" class=\"form-select\">
                            """
        for file in state['files']:
            yield """                            <option value=\""""
            yield str(file)
            yield """\">"""
            yield str(file)
            yield """</option>
                            """
        yield """                        </select>
                    </div>
                    <div class=\"mb-3\">
                        <label for=\"passesperline\" class=\"form-label\">Exposures per line</label>
                        <input id=\"passesperline\" class=\"form-control\" type=\"number\" value=\"1\" min=\"1\" max=\"10\"/>
                    </div>
                    <div class=\"mb-3\">
                        <label for=\"laserpower\" class=\"form-label\">Laser power [a.u]</label>
                        <input id=\"laserpower\" class=\"form-control\" type=\"number\" value=\"70\" min=\"50\" max=\"150\"/>
                    </div>
                </form>
            </div>
            <div class=\"modal-footer\">
                <button type=\"button\" class=\"btn btn-secondary\" data-bs-dismiss=\"modal\">Close</button>
                <button id=\"startprintbutton\" type=\"button\" class=\"btn btn-success\">Start</button>
            </div>
        </div>
    </div>
</div>


<!-- Upload file modal -->
<div class=\"modal fade\" id=\"uploadfilemodal\" tabindex=\"-1\" aria-labelledby=\"uploadfileModalLabel\" aria-hidden=\"true\">
    <div class=\"modal-dialog\">
        <div class=\"modal-content\">
            <div id=\"preuploadstate\">
                <div class=\"modal-header\">
                    <h1 class=\"modal-title fs-5\" id=\"uploadfileModalLabel\">Upload file</h1>
                    <button type=\"button\" class=\"btn-close\" data-bs-dismiss=\"modal\" aria-label=\"Close\"></button>
                </div>
                <div class=\"modal-body\">
                    <form id=\"uploadform\">
                        <div class=\"mb-3\">
                            <label for=\"formFile\" class=\"form-label\">File to upload</label>
                            <input class=\"form-control\" type=\"file\" id=\"uploadformFile\">
                        </div>
                    </form>
                    <div id=\"uploadprogressbar\" class=\"progress-bar progress-bar-striped progress-bar-animated bg-success\" role=\"progressbar\" style=\"width: 25%;display: none\" aria-valuenow=\"25\" aria-valuemin=\"0\" aria-valuemax=\"100\">25%</div>
                </div>
                <div class=\"modal-footer\">
                    <button id=\"uploadbutton\" type=\"button\"  class=\"btn btn-success\">Upload file</button>
                    <button id=\"uploadcancel\" type=\"button\" class=\"btn btn-secondary\" style=\"display: none\">Cancel</button>
                </div>
            </div>
        </div>
    </div>
</div>



<!-- Delete file modal -->
<div class=\"modal fade\" id=\"deletefilemodal\" tabindex=\"-1\" aria-labelledby=\"deletefileModalLabel\" aria-hidden=\"true\">
    <div class=\"modal-dialog\">
        <div class=\"modal-content\">
            <div class=\"modal-header\">
                <h1 class=\"modal-title fs-5\" id=\"exampleModalLabel\">Delete file</h1>
                <button type=\"button\" class=\"btn-close\" data-bs-dismiss=\"modal\" aria-label=\"Close\"></button>
            </div>
            <div class=\"modal-body\">
                <form>
                    <div class=\"mb-3\">
                        <label for=\"filetodelete\" class=\"form-label\">File to delete</label>
                        <select id=\"filetodelete\" class=\"form-select\">
                            """
        for file in state['files']:
            yield """                            <option value=\""""
            yield str(file)
            yield """\">"""
            yield str(file)
            yield """</option>
                            """
        yield """                        </select>
                    </div>
                </form>
            </div>
            <div class=\"modal-footer\">
                <button type=\"button\" class=\"btn btn-secondary\" data-bs-dismiss=\"modal\">Close</button>
                <button id=\"deletebutton\" type=\"button\" class=\"btn btn-success\">Delete file</button>
            </div>
        </div>
    </div>
</div>
"""
    yield from render7()
    yield """
        </div>
    </div>
    <br>
    <div class=\"row align-items-start\">
        <div class=\"col-2\">
        </div>
        <div class=\"col-4\">
            """
# Autogenerated file
    def render8(*a, **d):
        yield """<p class=\"text-center\">Stepsize [mm]</p>
<hr style=\"margin-top:-10px\"/>
<div id=\"stepsize\" class=\"list-group list-group-horizontal\">
  <a class=\"list-group-item list-group-item-success list-group-item-action\">0.1</a>
  <a class=\"list-group-item list-group-item-success list-group-item-action\">1</a>
  <a class=\"list-group-item list-group-item-success list-group-item-action active\">10</a>
  <a class=\"list-group-item list-group-item-success list-group-item-action\">100</a>
</div>
<script>

</script>"""
    yield from render8()
    yield """
        </div>
    </div>
</div>

<!-- Script for interactivity -->
<script src=\"static/home.js\"></script>

"""
# Autogenerated file
    def render9(*a, **d):
        yield """
    <!-- Bootstrap Bundle with Popper -->
    <script src=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js\" integrity=\"sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz\" crossorigin=\"anonymous\"></script>
    </body>
</html>
"""
    yield from render9()
    yield """
"""
