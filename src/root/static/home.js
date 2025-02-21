// want to extend some kudo's to brython https://github.com/brython-dev/brython
// jquery is not recommended, see https://flaviocopes.com/jquery/
var commandSocket;

function initializecommandSocket() {
    commandSocket = new WebSocket('ws://' + location.host + '/command');
    commandSocket.onclose = oncommandClose;
    commandSocket.onmessage = onMessage;
  }


// maintaining a long connection is a challenge
// microdot does not yet support socket
function oncommandClose(event) {
    setTimeout(initializeSocket, 2000);
}

/**
 * When X,Y,Z button is clicked stepsize with
 * vector indictating direction is to backend via websocket
 * @param {*} e 
 * @returns Nothing
 */
function movementClick(e){
    vector = e.target.id;
    // arrow icon has no ID, so if you click it.
    // you need to grab ID parent
    if (vector.length == 0){
    vector= e.target.parentNode.id;
    // clicks between buttons are ignored
    } else if (vector.includes('movement')){
    return;
    }
    steps = stepsize.querySelector('.active').innerHTML;
    commandSocket.send(JSON.stringify({"command":'move', "steps": steps, "vector": vector.split(',')}));
};

xymovement.addEventListener("click", movementClick);
zmovement.addEventListener("click", movementClick);


// enables one to select stepsize from 0.1 --> 1
stepsize.addEventListener("click", function (e) {
    stepsize.querySelector('.active').classList.remove('active');
    e.target.classList.add('active');
});


/**
 * Proceses click in the laserhead tab
 * @param {String} command Command that is executed 
 * @returns function 
 */
function laserheadCLick(command){
    return function(){commandSocket.send(JSON.stringify({"command": String(command)}))};
}
laser.addEventListener("click", laserheadCLick('togglelaser'));
motor.addEventListener("click", laserheadCLick('toggleprism'));
diode.addEventListener("click", laserheadCLick('diodetest'));


// delete file button
deletebutton.addEventListener("click", function (e) {
  fname = filetodelete[filetodelete.selectedIndex].text;
  commandSocket.send(JSON.stringify({"command": "deletefile", "file": fname}));
  window.location.href = '/';
});


// start print button
startprintbutton.addEventListener("click", function (e) {
  commandSocket.send(JSON.stringify({
  "command": "startprint", 
  "file": printjobfilename.value,
  "passes": passesperline.value,
  "laserpower": laserpower.value}));
  window.location.href = '/';
});

stopprintbutton.addEventListener("click", laserheadCLick('stopprint'));
pauseprintbutton.addEventListener("click", laserheadCLick('pauseprint'));

const sleep = ms => new Promise(r => setTimeout(r, ms));

startwebreplbutton.addEventListener("click", async function (e) {
  commandSocket.send(JSON.stringify({
  "command": "startwebrepl", }));
  window.alert("Please connect to webrepl port 8266.");
  await sleep(2000);
  if (window.location.href.indexOf('http://')==0){
    window.location = 'http://'+window.location.hostname+':8266'+window.location.pathname+window.location.search;
  }
});



changewifibutton.addEventListener("click", function (e) {
  commandSocket.send(JSON.stringify({
  "command": "changewifi", 
  "wifi": selectedwifi.value,
  "password": wifipassword.value,}));
  window.alert("Please connect to new IP address.");
  window.location.href = '/';
});

// a state is propagated from backend to the frontend
// on basis of the state either the printing or non printing
// options are displayed
// if printing, the current print state is updated
// if not printing, the laserhead tab is updated
 
var stateSocket;
window.addEventListener("load", onLoad);

function onLoad() {
  initializeSocket();
  initializecommandSocket();
}

function initializeSocket() {
    stateSocket = new EventSource(`/state`);
    stateSocket.onclose = onClose;
    stateSocket.onmessage = onMessage;
  }

function onClose(event) {
    console.log("Closing connection to server..");
    setTimeout(initializeSocket, 2000);
}


function updatemain(jsonData){
  if (jsonData['wifi']['connected']){
    wificonnectedtext.innerHTML = "Connected to wifi " + jsonData['wifi']['ssid'];
  } else{
    wificonnectedtext.innerHTML = "Not connected to wifi";
  }
  if (jsonData['printing']){
        printingstate.style.display = '';
        controlstate.style.display = 'none';
        filename.innerHTML = "Filename is " + String(jsonData['job']['filename']);
        lines.innerHTML = String(jsonData['job']['currentline']) + " of " + String(jsonData['job']['totallines']);
        printingtime.innerHTML = String(jsonData['job']['printingtime']) + " seconds elapsed";
        exposure.innerHTML = "Line is exposed " + String(jsonData['job']['passesperline']) + " times with a laser power of " + String(jsonData['job']['laserpower']) + " [a.u.]" 
        fraction = parseInt(jsonData['job']['currentline']) / parseInt(jsonData['job']['totallines']) * 100;
        progressbar.setAttribute('aria-valuenow', String(fraction));
        progressbar.setAttribute('style', 'width: ' + String(fraction) +'%' + ';');
        progressbar.innerHTML = String(fraction) + ' %';
    } else{
        printingstate.style.display = 'none';
        controlstate.style.display = '';

        if (jsonData['components']['rotating']){
            motor.innerHTML = "Turn motor off";
        } else {
            motor.innerHTML = "Turn motor on";
        }
        if (jsonData['components']['laser']){
            laser.innerHTML = "Turn laser off";
        } else {
            laser.innerHTML = "Turn laser on";
        }
        if (jsonData['components']['diodetest']){
            testresult.innerHTML = "Diode test successfull.";
        } else if (jsonData['components']['diodetest'] == false) {
            testresult.innerHTML = "Diode test failed.";
        } else {
            testresult.innerHTML = "Diode test not run.";
        }
    }
}



function onMessage(event) {
    let jsonData = JSON.parse(event.data);
    updatemain(jsonData);
}

async function upload(ev) {
    ev.preventDefault();
    const file = uploadformFile.files[0];
    if (!file) {
        window.alert("No file selected");
        return;
    }

    uploadcancel.style.display = '';
    uploadprogressbar.style.display = '';
    uploadbutton.style.display = 'none';
    uploadform.style.display = 'none';

    const req = new XMLHttpRequest();
    req.open("POST", '/upload');
    req.setRequestHeader('Content-Disposition', `attachment; filename="${file.name}"`);
    req.upload.addEventListener('progress', function (e) {
        const fraction = Math.round((e.loaded / e.total) * 100);
        uploadprogressbar.setAttribute('aria-valuenow', String(fraction));
        uploadprogressbar.style.width = fraction + '%'; // Simplified style
        uploadprogressbar.innerHTML = fraction + ' %';
    });

    req.addEventListener('load', function (e) {
        if (req.status === 200) {
            console.log('Upload accepted');
            console.log("File size after upload:", file.size); // Check file size
        } else {
            window.alert(`Upload failed with status ${req.status}`); // More informative error
        }
        window.location.href = '/'; // Redirect regardless of success/failure
    });

    req.addEventListener('error', function (e) {
        window.alert("Upload request received error");
        window.location.href = '/';
    });
    // Ideally you would use FormData but this doesnt work with the backend
    // files get corrupted
    req.send(file); 
    
    uploadcancel.addEventListener("click", function () {
        req.abort();
        window.location.href = '/';
    });
}

uploadbutton.addEventListener('click', upload);
