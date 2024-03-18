// want to extend some kudo's to brython https://github.com/brython-dev/brython
// jquery is not recommended, see https://flaviocopes.com/jquery/


const movementSocket = new WebSocket('ws://' + location.host + '/movement');

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
    movementSocket.send(JSON.stringify({"steps": steps, "vector": vector.split(',')}));
};

xymovement.addEventListener("click", movementClick);
zmovement.addEventListener("click", movementClick);


// enables one to select stepsize from 0.1 --> 1
stepsize.addEventListener("click", function (e) {
    stepsize.querySelector('.active').classList.remove('active')
    e.target.classList.add('active');
});



// Fetches state of laserhead
var stateSocket;
window.addEventListener("load", onLoad);

function onLoad() {
  initializeSocket();
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

  function onMessage(event) {
    console.log("State message received:", event);
    const obj = JSON.parse(event.data);
    console.log(obj);
  }