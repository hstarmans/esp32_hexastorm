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
 * @property {number[]} workspace_origin - The [x, y, z] starting offset vector
 */

/**
 * @typedef {Object} Components
 * @property {boolean} rotating - Is the motor turning?
 * @property {boolean} laser - Is the laser on?
 * @property {boolean|null} diodetest - null=not run, true=pass, false=fail
 * @property {number} spindle - Spindle PWM speed (0-255)
 * @property {number} fan - Fan PWM speed (0-255)
 */

/**
 * @typedef {Object} MachineState
 * @property {boolean} printing - Is the machine currently printing?
 * @property {boolean} paused - Is the print paused?
 * @property {PrintJob} job - The current job details
 * @property {Components} components - Hardware status
 * @property {number[]} mpos - Machine position mm [x, y, z]
 * @property {number[]} wpos - Workspace position mm [x, y, z]
 * @property {number} [notauthorized] - Optional flag if session is invalid
 */

// --- MAIN LOGIC ---

document.addEventListener("alpine:init", () => {
    
    // 1. CENTRAL API HELPER
    const api = {
        /**
         * Basic post request with error handling and state update
         * @param {string} url 
         * @param {Object} [payload={}] 
         */
        async post(url, payload = {}) {
            try {
                const res = await fetch(url, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                
                if (!res.ok) throw new Error(res.statusText);
                
                const data = await res.json();
                // @ts-ignore
                Alpine.store('machine').update(data);
                
            } catch (err) {
                console.error(url, err);
                alert("Command failed: " + err);
            }
        },

        /**
         * Universal function for all motion types
         * @param {number[]} position 
         * @param {boolean} [absolute=true] 
         * @param {boolean} [workspace=false] 
         */
        gotopoint(position, absolute = true, workspace = false) {
            this.post('/gotopoint', { position, absolute, workspace });
        }
    };

    // 2. GLOBAL MACHINE STORE
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
            singlefacet: false,
            workspace_origin: [0, 0, 0],
        },
        /** @type {Components} */
        components: {
            rotating: false,
            laser: false,
            diodetest: null,
            spindle: 0,
            fan: 0
        },
        mpos: [0.00, 0.00, 0.00], // machine position in mm
        wpos: [0.00, 0.00, 0.00], // workspace position in mm
        
        /**
         * Updates the store with data from the server
         * @param {MachineState} data - The JSON object from the backend
         */
        update(data) {
            if (data.notauthorized !== undefined) {
                window.location.reload();
                return;
            }

            const isValidPacket = (
                typeof data.printing !== 'undefined' &&
                data.job &&            
                data.components &&     
                data.mpos &&           
                data.wpos              
            );

            if (!isValidPacket) {
                console.warn("Ignored incomplete state packet:", data);
                return;
            }

            this.printing = data.printing;
            this.paused = data.paused || false; 
            this.job = data.job;
            this.components = data.components;
            this.mpos = data.mpos;
            this.wpos = data.wpos;
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

    // 3. MOVEMENT LOGIC (JOGGING / DIAL-IN)
    Alpine.data("movement", () => ({
        step: 10,

        init() {
            // @ts-ignore
            this.$el.addEventListener("step-change", (e) => {
                // @ts-ignore
                this.step = e.detail.step;
            });
        },

        /**
         * Handles clicking the arrow and homing buttons
         * @param {Event} e 
         */
        async handleClick(e) {
            // @ts-ignore
            const button = e.target.closest("[data-vector], [data-command]");
            if (!button || button.disabled) return;

            if (button.dataset.command === "home") {
                // Cast axes as a number array as well to keep things clean
                const axes = /** @type {number[]} */ (JSON.parse(button.dataset.axes));
                api.post("/home", { axes: axes });
            }
            else if (button.dataset.vector){
                // Tell TypeScript this is definitely an array of numbers
                const vector = /** @type {number[]} */ (JSON.parse(button.dataset.vector));
                
                const targetPos = vector.map(v => v * this.step);
                
                // Call central API with absolute=false for jogging
                api.gotopoint(targetPos, false, false);
            }
        }
    }));

    // 4. COMMANDS LOGIC (HARDWARE ACTIONS)
    Alpine.data("commands", () => ({
        
        toggleLaser() { api.post('/control/laser'); },
        togglePrism() { api.post('/control/prism'); },
        diodeTest()   { api.post('/control/diodetest'); },
        
        /** @param {number} value */
        setSpindle(value) { api.post('/control/spindle', { value: value }); },
        
        /** @param {number} value */
        setFan(value)     { api.post('/control/fan', { value: value }); },
        
        /** @param {number[]} [axes=[1, 1, 1]] */
        setWorkspaceZero(axes = [1, 1, 1]) { api.post('/setworkspacezero', { axes: axes }); },
        
        /** * @param {number[]} position 
         * @param {boolean} [workspace=true] 
         */
        goto(position, workspace = true) { 
            api.gotopoint(position, true, workspace); 
        },
        
        stopPrint()   { api.post('/print/control', { action: 'stop' }); },
        pausePrint()  { api.post('/print/control', { action: 'pause' }); },
        
        async reboot() {
            if(!confirm("Are you sure you want to reboot the system?")) return;

            try {
                await fetch("/reset", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" }
                });
                
                alert("System is rebooting. Page will reload in 10 seconds.");
                setTimeout(() => window.location.reload(), 10000);

            } catch (err) {
                alert("Reboot command failed: " + err);
            }
        },
    }));

    // 5. UPLOAD LOGIC
    Alpine.data("fileUploader", () => ({
        /** @type {File | null} */
        file: null,
        isUploading: false,
        progress: 0,
        errorMessage: '',
        /** @type {XMLHttpRequest | null} */
        xhr: null,

        /** @param {Event} e */
        handleFileSelect(e) {
            const target = /** @type {HTMLInputElement} */ (e.target);
            if (target.files && target.files.length > 0) {
                this.file = target.files[0];
            } else {
                this.file = null;
            }
            this.errorMessage = '';
            this.progress = 0;
        },

        upload() {
            if (!this.file) return;

            this.isUploading = true;
            this.progress = 0;
            this.errorMessage = '';

            this.xhr = new XMLHttpRequest();
            
            if (this.xhr.upload) {
                this.xhr.upload.addEventListener("progress", (e) => {
                    if (e.lengthComputable) {
                        this.progress = Math.round((e.loaded / e.total) * 100);
                    }
                });
            }

            this.xhr.addEventListener("load", () => {
                if (!this.xhr) return;

                if (this.xhr.status === 200) {
                    alert("Upload successful!");
                    window.location.reload(); 
                } else {
                    this.handleError(`Server Error: ${this.xhr.status} - ${this.xhr.statusText}`);
                }
                this.isUploading = false;
            });

            this.xhr.addEventListener("error", () => {
                this.handleError("Network Error during upload");
            });

            this.xhr.addEventListener("abort", () => {
                this.isUploading = false;
                this.progress = 0;
                this.errorMessage = "Upload cancelled";
            });

            this.xhr.open("POST", "/upload");
            this.xhr.setRequestHeader("Content-Disposition", `attachment; filename="${this.file.name}"`);
            this.xhr.setRequestHeader("Content-Type", "application/octet-stream");
            this.xhr.send(this.file);
        },

        cancel() {
            if (this.xhr) this.xhr.abort();
            this.isUploading = false;
        },

        /** @param {string} msg */
        handleError(msg) {
            this.errorMessage = msg;
            this.isUploading = false;
            this.progress = 0;
        }
    }));

    // 6. DELETE FILE LOGIC
    Alpine.data("fileDeleter", () => ({
        isDeleting: false,

        async deleteFile() {
            /** @type {HTMLSelectElement} */
            // @ts-ignore
            const select = this.$refs.fileSelector;
            
            if (!select || !select.value) {
                alert("Please select a file first.");
                return;
            }

            const filename = select.value;

            if (!confirm(`Are you sure you want to permanently delete "${filename}"?`)) {
                return;
            }

            this.isDeleting = true;

            try {
                const res = await fetch('/deletefile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file: filename })
                });

                if (res.ok) {
                    window.location.reload();
                } else {
                    const err = await res.json();
                    alert("Error: " + (err.error || res.statusText));
                }
            } catch (e) {
                alert("Network error: " + e);
            } finally {
                this.isDeleting = false;
            }
        }
    }));

    // 7. PRINT LAUNCHER LOGIC
    Alpine.data("printLauncher", () => ({
        /** @type {string} */
        selectedFile: '',
        /** @type {'laser' | 'cnc'} */
        jobMode: 'laser',
        /** @type {number|string} */
        laserPower: 100,
        /** @type {number|string} */
        exposure: 1,
        posX: 0,
        posY: 0,
        posZ: 0,
        singleFacet: false,
        isStarting: false,
        homeBeforePrint: true,
        useCustomStart: false,

        /** @this {PrintLauncher} */
        init() {
            this.$nextTick(() => {
                const select = /** @type {HTMLSelectElement | null} */ (this.$refs.fileSelect);
                if (select && select.options.length > 0) {
                    this.selectedFile = select.options[0].value;
                    this.detectMode(this.selectedFile);
                }
            });

            this.$watch('selectedFile', (value) => {
                // Typen dwingen naar string om "undefined" errors te voorkomen
                this.detectMode(String(value));
            });
        },

        /** * @this {PrintLauncher}
         * @param {string} filename 
         */
        detectMode(filename) {
            // Fallback check voor het geval de runtime toch iets geks doorgeeft
            if (!filename || typeof filename !== 'string') return;
            
            const parts = filename.split('.');
            const ext = parts.length > 1 ? parts.pop()?.toLowerCase() : '';
            
            if (ext && ['gcode', 'nc', 'tap'].includes(ext)) {
                this.jobMode = 'cnc';
            } else {
                this.jobMode = 'laser';
            }
        },

        /** @this {PrintLauncher} */
        async startPrint() {
            if (!this.selectedFile) {
                alert("Please select a file.");
                return;
            }

            this.isStarting = true;

            try {
                const payload = {
                    action: 'start',
                    file: this.selectedFile,
                    laserpower: Number(this.laserPower),
                    exposureperline: Number(this.exposure),
                    singlefacet: this.singleFacet,
                    home_before_print: this.homeBeforePrint,
                    use_custom_start: this.useCustomStart,
                    workspace_origin: [
                        Number(this.posX), 
                        Number(this.posY), 
                        Number(this.posZ)
                    ]
                };

                const res = await fetch('/print/control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) throw new Error(res.statusText);

            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                alert("Failed to start job: " + message);
            } finally {
                this.isStarting = false;
            }
        }
    }));

    // 8. SETTINGS MODAL LOGIC
    Alpine.data("machineSettings", () => ({
        // Fully stubbed out so Alpine doesn't panic on initial render
        wifi_login: {
            essid: '', ap_password: '', ssid: '', password: '',
            static_enabled: false, static_ip: '', dnsmask: '', gateway_ip: '', primary_dns: ''
        },
        motors: {
            motor_globals: {},
            non_tmc_keys: [],
            x: { steps_mm: 0, microstep_resolution: 16, current: 0, homing_dir: -1, offset_mm: 0, direction_inverted: false, stallguard_threshold: 0, coolstep_threshold: 0 },
            y: { steps_mm: 0, microstep_resolution: 16, current: 0, homing_dir: -1, offset_mm: 0, direction_inverted: false, stallguard_threshold: 0, coolstep_threshold: 0 },
            z: { steps_mm: 0, microstep_resolution: 16, current: 0, homing_dir: -1, offset_mm: 0, direction_inverted: false, stallguard_threshold: 0, coolstep_threshold: 0 }
        },
        tools: {
            laser: { offset_x: 0, offset_y: 0 }
        },
        isLoading: false,

        /** * Automatically fetch settings from the backend when this component initializes 
         */
        async init() {
            this.isLoading = true;
            try {
                const res = await fetch('/api/settings');
                if (res.ok) {
                    const data = await res.json();
                    
                    // Merge incoming data over the stubbed defaults
                    this.wifi_login = { ...this.wifi_login, ...data.wifi_login };
                    this.motors = { ...this.motors, ...data.motors };
                    this.tools = { ...this.tools, ...data.tools };
                } else {
                    console.error("Failed to load configuration");
                }
            } catch (err) {
                console.error("Network error loading settings:", err);
            } finally {
                this.isLoading = false;
            }
        },

        /**
         * Save the modified settings back to the JSON file on the ESP32
         */
        async saveSettings() {
            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        wifi_login: this.wifi_login,
                        motors: this.motors,
                        tools: this.tools
                    })
                });

                if (res.ok) {
                    alert("Settings saved successfully! A reboot may be required for some changes to take effect.");
                } else {
                    alert("Failed to save settings.");
                }
            } catch (err) {
                alert("Error saving settings: " + err);
            }
        },

        async factoryReset() {
            if (!confirm("WARNING: This will wipe all calibration and Wi-Fi data. Are you sure?")) return;
            
            try {
                await fetch('/api/settings/reset', { method: 'POST' });
                alert("Factory reset triggered. Rebooting...");
                setTimeout(() => window.location.reload(), 5000);
            } catch (err) {
                alert("Reset failed: " + err);
            }
        }
    }));
});

// --- SERVER SENT EVENTS (SSE) ---

/** @type {EventSource | null} */
let stateSocket = null;

function initializeSocket() {
    if (stateSocket && stateSocket.readyState !== EventSource.CLOSED) return;

    stateSocket = new EventSource('/state');
    
    stateSocket.onmessage = (event) => {
        if(event.type === 'ping' || event.data === 'ping') return;

        const data = JSON.parse(event.data);
        // @ts-ignore
        Alpine.store('machine').update(data);
    };

    stateSocket.onerror = (_err) => {
        console.log("SSE connection lost, retrying in 2s...");
        if(stateSocket) stateSocket.close();
        setTimeout(initializeSocket, 2000);
    };
}

// Start SSE connection on window load
window.addEventListener("load", initializeSocket);