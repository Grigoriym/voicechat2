// Conversation screen (ui/chat.html). Extracted from the pre-rework
// index.html: push-to-talk recording (native MediaRecorder), the
// experimental VAD path, websocket streaming, transcript, and latency
// metrics. The scenario + conversation model + grammar corrector model are
// chosen on the Setup screen and read from sessionStorage here rather than
// from on-page dropdowns.

const SCENARIO_STORAGE_KEY = "vc2-scenario";
const MODEL_STORAGE_KEY = "vc2-model";
const GRAMMAR_MODEL_STORAGE_KEY = "vc2-grammar-model";

const scenarioId = sessionStorage.getItem(SCENARIO_STORAGE_KEY);
const modelId = sessionStorage.getItem(MODEL_STORAGE_KEY);
const grammarModelId = sessionStorage.getItem(GRAMMAR_MODEL_STORAGE_KEY);

if (!scenarioId || !modelId || !grammarModelId) {
    // No choice made yet (e.g. a direct/bookmarked visit) — send the user
    // back to Setup rather than starting a conversation with nothing set.
    window.location.href = "/";
}

// Elements to update
const vadToggle = document.getElementById("vadToggle");
const recordButton = document.getElementById("recordButton");
const status = document.getElementById("status");
const logArea = document.getElementById("logArea");
const timerDisplay = document.getElementById("timer");

// Recolor the status badge whenever its text changes, instead of touching
// every one of the many call sites that set it directly.
const STATUS_CLASS_MAP = {
    ready: "status-ready",
    "recording...": "status-recording",
    "processing...": "status-processing",
    speaking: "status-speaking",
};
new MutationObserver(() => {
    const text = status.textContent.trim().toLowerCase();
    let cls = STATUS_CLASS_MAP[text];
    if (!cls) cls = text.includes("error") || text.includes("disconnect") ? "status-error" : "status-ready";
    status.className = "status-badge " + cls;
}).observe(status, { childList: true, characterData: true, subtree: true });

// Network
let startTime;
let socket;
let isProcessing = false;
let latencyIntervalId = null;
let ping = null;

// Recording
let isRecording = false;
let recordingStartTime;
let recorder;
let timerInterval;

// VAD
let isVADEnabled = false;
let myvad;

// Text
let currentAIResponse = "";
let aiMessageElement = null;
let isAIResponding = false;
let currentTurn = null;

// Chat bubbles, keyed by `${turn}-${role}` (role matches the server's
// grammar_check "user"/"assistant", not the display label) so a later
// grammar_check message can find the bubble it belongs to.
const messageElements = {};
const ROLE_LABEL = { user: "User", assistant: "AI" };
const ROLE_CLASS = { user: "user-message", assistant: "ai-message" };

// Playback
let audioQueue = [];
let isPlaying = false;

async function initActiveContextDisplay() {
    const el = document.getElementById("activeContext");
    try {
        const resp = await fetch("/api/scenarios");
        const data = await resp.json();
        const scenario = data.scenarios.find((s) => s.id === scenarioId);
        el.textContent =
            `scenario: ${scenario ? scenario.label : scenarioId} · model: ${modelId} · ` +
            `corrector: ${grammarModelId}`;
    } catch (error) {
        el.textContent = `scenario: ${scenarioId} · model: ${modelId} · corrector: ${grammarModelId}`;
        log(`Error loading scenario label: ${error.message}`);
    }
}

// Ping every second
function startLatencyMeasurement() {
    if (latencyIntervalId === null) {
        latencyIntervalId = setInterval(measureLatency, 1000);
    }
}

function stopLatencyMeasurement() {
    if (latencyIntervalId !== null) {
        clearInterval(latencyIntervalId);
        latencyIntervalId = null;
    }
}

function measureLatency() {
    if (!isProcessing) {
        ping = performance.now();
        socket.send(JSON.stringify({ type: "ping" }));
    }
}

function updateLatencyDisplay(latency) {
    const latencyElement = document.getElementById("networkLatency");
    if (latencyElement) {
        latencyElement.textContent = `${latency.toFixed(2)}ms`;
    }
}

// VAD
vadToggle.addEventListener("click", toggleVAD);
async function toggleVAD() {
    if (isVADEnabled) {
        isVADEnabled = false;
        vadToggle.textContent = "Enable Voice Auto Detection (experimental)";
        if (myvad) {
            await myvad.pause();
        }
        updateStatus("VAD disabled");
    } else {
        isVADEnabled = true;
        vadToggle.textContent = "Disable Voice Auto Detection (experimental)";
        initializeVAD();
        updateStatus("VAD enabled");
    }
}
async function startVAD() {
    if (isVADEnabled) {
        if (myvad) {
            await myvad.start();
        }
        updateStatus("VAD enabled");
    }
}
async function pauseVAD() {
    if (isVADEnabled) {
        if (myvad) {
            await myvad.pause();
        }
        updateStatus("VAD paused");
    }
}

// START monkeypatch
Recorder.prototype.encodeAudio = function (audioFloat32Array) {
    return new Promise((resolve, reject) => {
        const originalOndataavailable = this.ondataavailable;
        const originalOnstart = this.onstart;
        const originalOnstop = this.onstop;

        let encodedChunks = [];

        this.ondataavailable = (typedArray) => {
            encodedChunks.push(typedArray);
        };

        this.onstart = () => {
            if (this.encoder && this.encoder.postMessage) {
                this.encoder.postMessage({
                    command: "encode",
                    buffers: [audioFloat32Array],
                });
            }
        };

        this.onstop = () => {
            const opusBlob = new Blob(encodedChunks, { type: "audio/ogg; codecs=opus" });

            this.ondataavailable = originalOndataavailable;
            this.onstart = originalOnstart;
            this.onstop = originalOnstop;

            resolve(opusBlob);
        };

        this.start()
            .then(() => {
                this.stop();
            })
            .catch(reject);
    });
};
// END monkeypatch

async function initializeVAD() {
    try {
        if (!myvad) {
            myvad = await vad.MicVAD.new({
                onSpeechEnd: async (audio) => {
                    let opusBlob;

                    console.log("Speech ended, audio length:", audio.length);

                    clearInterval(timerInterval);

                    try {
                        opusBlob = await recorder.encodeAudio(audio);
                        console.log("Encoding complete, blob size:", opusBlob.size);

                        if (!socket || socket.readyState !== WebSocket.OPEN) {
                            log("WebSocket is not open. Reinitializing...");
                            try {
                                await initializeWebSocketAsync();
                            } catch (error) {
                                log(`Error reinitializing WebSocket: ${error.message}`);
                                return;
                            }
                        }

                        if (socket && socket.readyState === WebSocket.OPEN) {
                            log(`Sending audio file: ${opusBlob.size} bytes`);
                            socket.send(opusBlob);
                            log("Audio file sent successfully");
                            socket.send(JSON.stringify({ action: "stop_recording" }));
                            log("Sent stop_recording message");
                        } else {
                            log("WebSocket is not open. Cannot send audio.");
                        }
                    } catch (error) {
                        console.error("Error encoding audio:", error);
                    }
                },
                onVADMisfire: () => {
                    log("VAD misfire detected");
                },
            });
        }
        await myvad.start();
        updateStatus("VAD initialized and started");
    } catch (error) {
        console.error("Error initializing VAD:", error);
        updateStatus("Error initializing VAD");
    }
}

function updateStatus(message) {
    status.textContent = message;
}

async function startRecording() {
    if (!isRecording) {
        stopLatencyMeasurement();

        currentAIResponse = "";
        aiMessageElement = null;
        isAIResponding = false;

        isRecording = true;
        recordingStartTime = Date.now();
        updateTimerDisplay();

        // Chrome suspends AudioContext until a user gesture explicitly
        // resumes it; without this the recorder produces no audio data
        // even though start()/stop() and the UI both look normal.
        if (audioContext.state === "suspended") {
            await audioContext.resume();
            log("AudioContext resumed (was suspended)");
        }

        recorder.start();
        recordButton.classList.add("recording");
        status.textContent = "Recording...";
    }
}

async function stopRecording() {
    if (isRecording) {
        isRecording = false;
        recordButton.classList.remove("recording");
        try {
            await recorder.stop();
            log("Recording stopped successfully");
            status.textContent = "Processing...";

            if (!socket || socket.readyState !== WebSocket.OPEN) {
                log("WebSocket is not open. Reinitializing...");
                try {
                    await initializeWebSocketAsync();
                } catch (error) {
                    log(`Error reinitializing WebSocket: ${error.message}`);
                    return;
                }
            }

            const stopMessage = JSON.stringify({ action: "stop_recording" });
            log(`Sending stop_recording message: ${stopMessage}`);
            socket.send(stopMessage);
        } catch (error) {
            log(`Error stopping recording: ${error.message}`);
        }
    }
}

function updateTimerDisplay() {
    if (isRecording) {
        const elapsed = Date.now() - recordingStartTime;
        const minutes = Math.floor(elapsed / 60000);
        const seconds = Math.floor((elapsed % 60000) / 1000);
        const milliseconds = elapsed % 1000;
        timerDisplay.textContent = `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}:${milliseconds.toString().padStart(3, "0")}`;
        requestAnimationFrame(updateTimerDisplay);
    }
}

// Button event listeners
recordButton.addEventListener("mousedown", startRecording);
recordButton.addEventListener("mouseup", stopRecording);
recordButton.addEventListener("mouseleave", stopRecording);

// Spacebar event listeners
document.addEventListener("keydown", (event) => {
    if (event.code === "Space" && !isRecording) {
        event.preventDefault();
        startRecording();
    }
});

document.addEventListener("keyup", (event) => {
    if (event.code === "Space") {
        event.preventDefault();
        stopRecording();
    }
});

// Touch event listeners for mobile devices
recordButton.addEventListener("touchstart", (event) => {
    event.preventDefault();
    startRecording();
});

recordButton.addEventListener("touchend", (event) => {
    event.preventDefault();
    stopRecording();
});

function playNextAudio() {
    if (!isPlaying) {
        startVAD();
    }

    if (audioQueue.length === 0 || isPlaying) {
        return;
    }

    isPlaying = true;
    pauseVAD();

    const audioBlob = audioQueue.shift();
    const audio = new Audio(URL.createObjectURL(audioBlob));

    audio.onended = () => {
        isPlaying = false;
        playNextAudio();
    };

    audio.onerror = (error) => {
        log(`Error playing audio: ${error.message}`);
        isPlaying = false;
        playNextAudio();
    };

    audio.play().catch((error) => {
        log(`Error starting audio playback: ${error.message}`);
        isPlaying = false;
        playNextAudio();
    });
}

function queueAudioForPlayback(audioBlob) {
    audioQueue.push(audioBlob);
    playNextAudio();
}

function log(message) {
    const timestamp = new Date().toISOString();
    logArea.innerHTML = `${timestamp} - ${message}<br>` + logArea.innerHTML;
}

function updateTimer() {
    const elapsed = Date.now() - startTime;
    const minutes = Math.floor(elapsed / 60000);
    const seconds = Math.floor((elapsed % 60000) / 1000);
    const milliseconds = elapsed % 1000;
    timerDisplay.textContent = `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}:${milliseconds.toString().padStart(3, "0")}`;
}

let audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

async function createSourceNode() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        return { stream, sourceNode: audioContext.createMediaStreamSource(stream) };
    } catch {
        // If we don't resample then recording will be 48K, 4x too slow...
        recordButton.style.display = "none";
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        return { stream, sourceNode: audioContext.createMediaStreamSource(stream) };
    }
}

let analyser;

function startMicMeter(sourceNode) {
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 512;
    sourceNode.connect(analyser);

    const data = new Uint8Array(analyser.fftSize);
    const fill = document.getElementById("micMeterFill");
    const hint = document.getElementById("micMeterHint");

    function tick() {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (let i = 0; i < data.length; i++) {
            peak = Math.max(peak, Math.abs(data[i] - 128));
        }
        const level = Math.min(1, peak / 100); // 128 is silence; ~100 is loud
        fill.style.width = `${(level * 100).toFixed(0)}%`;
        fill.classList.toggle("loud", level > 0.4 && level <= 0.8);
        fill.classList.toggle("clip", level > 0.8);
        if (level > 0.03) {
            hint.textContent = "Microphone receiving audio ✓";
        } else {
            hint.textContent = "No audio detected — speak to test the meter";
        }
        requestAnimationFrame(tick);
    }
    tick();
}

function reportCapture(blob, durationS) {
    const captureEl = document.getElementById("lastCapture");
    if (blob.size < 500) {
        captureEl.className = "bad";
        captureEl.textContent = `⚠️ Last capture: ${blob.size} bytes / ${durationS}s — essentially empty, nothing was recorded`;
    } else {
        captureEl.className = "ok";
        captureEl.textContent = `✓ Last capture: ${blob.size} bytes / ${durationS}s`;
    }
}

// Uses the browser's native MediaRecorder rather than the bundled
// symbl-opus-encdec library — see CLAUDE.md ("Client-side recording") for
// why. VAD still uses the old library's monkeypatched encodeAudio() above
// and has the same root issue; not fixed here since push-to-talk is the
// primary path.
async function initializeRecorder() {
    const { stream, sourceNode } = await createSourceNode();
    startMicMeter(sourceNode);

    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
    const mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

    let audioChunks = [];
    mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
            log(`Data available: ${e.data.size} bytes`);
            audioChunks.push(e.data);
        }
    };

    recorder = {
        start() {
            audioChunks = [];
            mediaRecorder.start();
            log("Recording started");
            status.textContent = "Recording...";
            startTime = Date.now();
            timerInterval = setInterval(updateTimer, 10);
            return Promise.resolve();
        },
        stop() {
            return new Promise((resolve) => {
                mediaRecorder.onstop = () => {
                    log("Recording stopped");
                    status.textContent = "Processing...";
                    clearInterval(timerInterval);

                    const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
                    const durationS = ((Date.now() - recordingStartTime) / 1000).toFixed(1);
                    audioChunks = [];
                    reportCapture(blob, durationS);

                    if (socket && socket.readyState === WebSocket.OPEN) {
                        log(`Sending audio file: ${blob.size} bytes`);
                        socket.send(blob);
                    } else {
                        log("WebSocket is not open. Cannot send audio.");
                    }
                    resolve();
                };
                mediaRecorder.stop();
            });
        },
    };
}

function initializeWebSocketAsync() {
    return new Promise((resolve, reject) => {
        const currentUrl = window.location;
        const wsProtocol = currentUrl.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${wsProtocol}//${currentUrl.host}/ws`;
        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            log("WebSocket connected");
            status.textContent = "Ready";
            recordButton.disabled = false;
            startLatencyMeasurement();
            socket.send(JSON.stringify({ action: "set_model", model: modelId }));
            socket.send(
                JSON.stringify({ action: "set_grammar_model", grammar_model: grammarModelId })
            );
            socket.send(JSON.stringify({ action: "set_scenario", scenario: scenarioId }));
            resolve(socket);
        };

        socket.onclose = (event) => {
            log(`WebSocket disconnected. Code: ${event.code}, Reason: ${event.reason}`);
            status.textContent = "Disconnected";
            recordButton.disabled = true;
            stopLatencyMeasurement();
            reject(new Error("WebSocket closed"));
        };

        socket.onerror = (error) => {
            log(`WebSocket error: ${error.message}`);
            status.textContent = "Error";
            reject(error);
        };

        socket.onmessage = (event) => {
            log(`Received message from server: ${typeof event.data}`);
            if (event.data instanceof Blob) {
                queueAudioForPlayback(event.data);
            } else {
                try {
                    const message = JSON.parse(event.data);

                    if (message.type !== "pong") {
                        log(`Parsed server message: ${JSON.stringify(message)}`);
                    }

                    if (message.type === "pong") {
                        const latency = performance.now() - ping;
                        updateLatencyDisplay(latency);
                    } else if (message.type === "text") {
                        updateAIResponse(message.content);
                    } else if (message.type === "transcription") {
                        currentTurn = message.turn;
                        displayMessage("user", message.content, currentTurn);
                        currentAIResponse = "";
                        aiMessageElement = null;
                        isAIResponding = true;
                    } else if (message.type === "grammar_check") {
                        applyGrammarCheck(message);
                    } else if (message.type === "latency_metrics") {
                        updateLatencyMetrics(message.metrics);
                    } else if (message.type === "processing_complete") {
                        status.textContent = "Ready";
                        isProcessing = false;
                        isAIResponding = false;
                        if (aiMessageElement) {
                            const cursor = aiMessageElement.querySelector(".ai-cursor");
                            if (cursor) cursor.remove();
                        }
                    } else if (message.type === "error") {
                        log(`Error from server: ${message.message}`);
                        status.textContent = "Error";
                    }
                } catch (error) {
                    log(`Error parsing server message: ${error.message}`);
                }
            }
        };
    });
}

function updateLatencyMetrics(metrics) {
    document.getElementById("totalVoiceToVoice").textContent = `${(metrics.total_voice_to_voice * 1000).toFixed(1)}ms`;
    document.getElementById("srtDuration").textContent = `${(metrics.srt_duration * 1000).toFixed(1)}ms`;
    document.getElementById("llmTTFT").textContent = `${(metrics.llm_ttft * 1000).toFixed(1)}ms`;
    document.getElementById("llmTTFS").textContent = `${(metrics.llm_ttfs * 1000).toFixed(1)}ms`;
    document.getElementById("ttsDuration").textContent = `${(metrics.tts_duration * 1000).toFixed(1)}ms`;
}

function displayMessage(role, content, turn) {
    const conversationLog = document.getElementById("conversationLog");
    const messageElement = document.createElement("p");
    messageElement.className = ROLE_CLASS[role] || `${role}-message`;
    if (turn !== undefined && turn !== null) {
        messageElement.dataset.turn = turn;
        messageElement.dataset.role = role;
        messageElements[`${turn}-${role}`] = messageElement;
    }
    // Text lives in its own child span so a grammar_check badge/correction
    // can be appended as a sibling later without getting wiped by
    // updateAIResponse's innerHTML rewrites while the AI reply streams in.
    const body = document.createElement("span");
    body.className = "message-body";
    body.textContent = `${ROLE_LABEL[role] || role}: ${content}`;
    messageElement.appendChild(body);
    conversationLog.appendChild(messageElement);
    conversationLog.scrollTop = conversationLog.scrollHeight;
    return messageElement;
}

function updateAIResponse(newContent) {
    currentAIResponse += newContent;
    if (!aiMessageElement) {
        aiMessageElement = displayMessage("assistant", "", currentTurn);
    }
    const body = aiMessageElement.querySelector(".message-body");
    body.innerHTML = `AI: ${currentAIResponse}${isAIResponding ? '<span class="ai-cursor"></span>' : ""}`;
    const conversationLog = document.getElementById("conversationLog");
    conversationLog.scrollTop = conversationLog.scrollHeight;
}

// Looks up the chat bubble a grammar_check message belongs to and appends a
// ✓ badge (correct) or a "→ corrected: ..." line (incorrect). Skips
// silently if the bubble's gone (e.g. page reloaded mid-check).
function applyGrammarCheck(message) {
    const element = messageElements[`${message.turn}-${message.role}`];
    if (!element) return;

    const existingNote = element.querySelector(".grammar-note");
    if (existingNote) existingNote.remove();

    if (message.correct) {
        const badge = document.createElement("span");
        badge.className = "grammar-note grammar-ok";
        badge.textContent = "✓";
        element.appendChild(badge);
    } else {
        const correction = document.createElement("div");
        correction.className = "grammar-note grammar-correction";
        correction.textContent = `→ corrected: ${message.corrected}`;
        element.appendChild(correction);
    }
}

// Initialize recorder and WebSocket when the page loads
window.onload = async () => {
    await initActiveContextDisplay();
    await initializeRecorder();
    log("Recorder ready (MediaRecorder)");

    try {
        await initializeWebSocketAsync();
        log("Application initialized");
    } catch (error) {
        log(`Error initializing application: ${error.message}`);
    }
};
