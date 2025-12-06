// @ts-check

/**
 * TYPE DEFINITIONS
 * These help VS Code understand your data structures.
 */

/**
 * @typedef {Object} PrintJob
 * @property {string} filename - The name of the file being printed
 * @property {number} currentline - Current line number
 * @property {number} totallines - Total lines in file
 * @property {number} printingtime - Elapsed time in seconds
 * @property {number} exposureperline - Exposure count
 * @property {number} laserpower - Laser power level
 * @property {boolean} singlefacet - Whether single facet mode is on
 */

/**
 * @typedef {Object} Components
 * @property {boolean} rotating - Is the motor turning?
 * @property {boolean} laser - Is the laser on?
 * @property {boolean|null} diodetest - null=not run, true=pass, false=fail
 */

/**
 * @typedef {Object} MachineState
 * @property {boolean} printing - Is the machine currently printing?
 * @property {boolean} paused - Is the print paused?
 * @property {PrintJob} job - The current job details
 * @property {Components} components - Hardware status
 */

// --- MAIN LOGIC ---

document.addEventListener("alpine:init", () => {
    
    // 1. GLOBAL STORE
    // We explicitly type 'this' in comments so VS Code knows what 'this' refers to.
    Alpine.store('machine', {
        printing: false,
        paused: false,
        /** @type {PrintJob} */
        job: {
            filename: '',
            currentline: 0,
            totallines: 0,
            printingtime: 0,
            exposureperline: 0,
            laserpower: 0,
            singlefacet: false
        },
        /** @type {Components} */
        components: {
            rotating: false,
            laser: false,
            diodetest: null
        },

        /**
         * Updates the store with data from the server
         * @param {MachineState} data - The JSON object from the backend
         */
        update(data) {
            // VS Code will now validate that 'data' has the right properties
            this.printing = data.printing;
            this.paused = data.paused || false;
            
            if (data.job) this.job = data.job;
            if (data.components) this.components = data.components;
        },

        /**
         * Calculates percentage for the progress bar
         * @returns {number} 0-100
         */
        get progressPercent() {
            if (!this.job || this.job.totallines === 0) return 0;
            return Math.round((this.job.currentline / this.job.totallines) * 100);
        }
    });

    // 2. MOVEMENT LOGIC
    Alpine.data("movement", () => ({
        step: 10,

        init() {
            // @ts-ignore - Custom events sometimes confuse TS, ignore is safe here
            this.$el.addEventListener("step-change", (e) => {
                // @ts-ignore
                this.step = e.detail.step;
            });
        },

        /**
         * Handles clicking the arrow buttons
         * @param {Event} e 
         */
        async handleClick(e) {
            // @ts-ignore - closest is valid on target
            const button = e.target.closest("[data-vector]");
            if (!button || button.disabled) return;

            const vector = JSON.parse(button.dataset.vector);
            
            try {
                await fetch("/move", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ vector: vector, steps: this.step }),
                });
            } catch (err) {
                console.error("Move failed", err);
            }
        }
    }));

    // 3. COMMANDS LOGIC
    Alpine.data("commands", () => ({
        
        /**
         * Sends a generic POST request to the backend.
         * @param {string} url - The endpoint path (e.g., '/control/laser')
         * @param {Object} [payload] - Optional JSON body to send
         * @returns {Promise<void>}
         */
        async post(url, payload = {}) {
          try {
              const res = await fetch(url, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(payload),
              });
              
              if (!res.ok) throw new Error(res.statusText);
              
              // UI flicker is possible if the SSE event is delayed
              const data = await res.json();
              Alpine.store('machine').update(data);
              
          } catch (err) {
              console.error(url, err);
              alert("Command failed: " + err);
          }
        },

        // Point to the new specific URLs
        toggleLaser() { this.post('/control/laser'); },
        togglePrism() { this.post('/control/prism'); },
        diodeTest()   { this.post('/control/diodetest'); },
        
        stopPrint()   { this.post('/print/control', { action: 'stop' }); },
        pausePrint()  { this.post('/print/control', { action: 'pause' }); },

        startPrint() {
            /** @type {HTMLSelectElement} */
            // @ts-ignore
            const fileSelect = document.getElementById('printjobfilename');
            /** @type {HTMLInputElement} */
            // @ts-ignore
            const power = document.getElementById('laserpower');
            /** @type {HTMLInputElement} */
            // @ts-ignore
            const exposure = document.getElementById('exposureperline');
            /** @type {HTMLInputElement} */
            // @ts-ignore
            const facet = document.getElementById('singlefacet');

            if (!fileSelect || !power) return;
            this.post('/print/control', {
                action: 'start',
                file: fileSelect.value,
                laserpower: power.value,
                exposureperline: exposure.value,
                singlefacet: facet.checked
            });
            // Reload is actually not needed if you rely on SSE to switch the view!
            // But if you want to be safe:
            setTimeout(() => window.location.reload(), 500);
        },

        // deleteFile() {
        //      /** @type {HTMLSelectElement} */
        //      // @ts-ignore
        //      const select = document.getElementById('filetodelete');
        //      if(!select) return;

        //      const fname = select.options[select.selectedIndex].text;
        //      this.send('deletefile', { file: fname });
        // },
        async reboot() {
            if(!confirm("Are you sure you want to reboot the system?")) return;

            try {
                await fetch("/reset", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                
                alert("System is rebooting. Page will reload in 10 seconds.");
                
                setTimeout(() => {
                    window.location.reload();
                }, 10000);

            } catch (err) {
                alert("Reboot command failed: " + err);
            }
        },
    }));
});




// --- SERVER SENT EVENTS (SSE) ---

/** @type {EventSource | null} */
let stateSocket = null;

function initializeSocket() {
    if (stateSocket && stateSocket.readyState !== EventSource.CLOSED) return;

    stateSocket = new EventSource('/state');
    
    stateSocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        // @ts-ignore - Alpine isn't globally typed in window, so we ignore
        Alpine.store('machine').update(data);
    };

    stateSocket.onerror = (_err) => {
        console.log("SSE connection lost, retrying in 2s...");
        if(stateSocket) stateSocket.close();
        setTimeout(initializeSocket, 2000);
    };
}

// Start SSE on load
window.addEventListener("load", initializeSocket);