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
    // 4. UPLOAD LOGIC
    /**
     * @typedef {Object} FileUploader
     * @property {File | null} file - The file object selected by the user
     * @property {boolean} isUploading - UI state for showing progress bar vs input
     * @property {number} progress - Upload percentage (0-100)
     * @property {string} errorMessage - Error text to display
     * @property {XMLHttpRequest | null} xhr - The active XHR request object
     * @property {(e: Event) => void} handleFileSelect - Input change handler
     * @property {() => void} upload - Starts the binary upload
     * @property {() => void} cancel - Aborts the active upload
     * @property {(msg: string) => void} handleError - Helper to set error state
     */

    Alpine.data("fileUploader", () => ({
        /** @type {File | null} */
        file: null,
        isUploading: false,
        progress: 0,
        errorMessage: '',
        /** @type {XMLHttpRequest | null} */
        xhr: null,

        /**
         * Triggered when file input changes
         * @param {Event} e 
         */
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

        /**
         * Performs the raw binary upload
         */
        upload() {
            if (!this.file) return;

            this.isUploading = true;
            this.progress = 0;
            this.errorMessage = '';

            // We use XHR because fetch() doesn't support upload progress streams easily
            this.xhr = new XMLHttpRequest();
            
            // Setup listeners
            if (this.xhr.upload) {
                this.xhr.upload.addEventListener("progress", (e) => {
                    if (e.lengthComputable) {
                        this.progress = Math.round((e.loaded / e.total) * 100);
                    }
                });
            }

            this.xhr.addEventListener("load", () => {
                // We check for 'this.xhr' existence to satisfy strict null checks, 
                // though logically it exists here.
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

            // Open Connection
            this.xhr.open("POST", "/upload");

            // CRITICAL: Matches your Python backend expectation
            this.xhr.setRequestHeader("Content-Disposition", `attachment; filename="${this.file.name}"`);
            this.xhr.setRequestHeader("Content-Type", "application/octet-stream");

            // Send raw file bytes
            this.xhr.send(this.file);
        },

        cancel() {
            if (this.xhr) {
                this.xhr.abort();
            }
            this.isUploading = false;
        },

        /**
         * @param {string} msg 
         */
        handleError(msg) {
            this.errorMessage = msg;
            this.isUploading = false;
            this.progress = 0;
        }
    }));
    // 5. DELETE FILE LOGIC
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

            // Double confirmation usually good for deletions
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
                    // Success: Reload page to update the Jinja file list
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
    // 6. PRINT LAUNCHER LOGIC
    /**
     * @typedef {Object} PrintLauncher
     * @property {string} selectedFile
     * @property {number|string} laserPower
     * @property {number|string} exposure
     * @property {boolean} singleFacet
     * @property {boolean} isStarting
     * @property {() => void} init
     * @property {() => Promise<void>} startPrint
     * * // Add Alpine Magic Properties here so TS knows they exist:
     * @property {(callback: Function) => void} $nextTick
     * @property {Object.<string, HTMLElement>} $refs
     */

    Alpine.data("printLauncher", () => ({
        /** @type {string} */
        selectedFile: '',
        /** @type {number|string} */
        laserPower: 100,
        /** @type {number|string} */
        exposure: 1,
        singleFacet: false,
        isStarting: false,

        /**
         * Initialize default values
         */
        init() {
            // We cast 'this' to the Type defined above so VS Code sees $nextTick
            const self = /** @type {PrintLauncher} */ (/** @type {unknown} */ (this));

            self.$nextTick(() => {
                const select = /** @type {HTMLSelectElement} */ (self.$refs.fileSelect);
                
                if (select && select.options.length > 0) {
                    self.selectedFile = select.options[0].value;
                }
            });
        },

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
                    singlefacet: this.singleFacet
                };

                const res = await fetch('/print/control', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) throw new Error(res.statusText);

                setTimeout(() => window.location.reload(), 500);

            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                alert("Failed to start print: " + message);
            } finally {
                this.isStarting = false;
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