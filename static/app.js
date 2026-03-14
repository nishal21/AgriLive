/**
 * app.js — AgriLive Frontend Client (Field Companion Edition)
 *
 * Handles:
 *  1. WebSocket connection to the FastAPI backend (/ws) with auto-reconnect
 *  2. Microphone capture → raw 16-bit PCM at 16 kHz mono → base64 → WS
 *  3. Webcam capture → JPEG at 1 FPS → base64 → WS
 *  4. Receiving audio (24 kHz 16-bit PCM) from server → AudioContext playback
 *  5. Receiving text transcripts from server → UI display
 *  6. Bottom-sheet drawer for transcript
 *  7. Crop analysis via /api/analyze
 *  8. Session timer, haptic feedback, contextual status
 */

// ========================================================================
// State
// ========================================================================
let ws = null;
let audioContext = null;
let micStream = null;
let micProcessor = null;
let webcamStream = null;
let webcamInterval = null;
let isSessionActive = false;
let nextPlayTime = 0;

let currentFacingMode = "environment";
let activeAudioSources = [];
let audioQueue = [];
let isPlaybackStarted = false;
const JITTER_BUFFER_THRESHOLD = 3;

// Reconnect state
let reconnectAttempts = 0;
const MAX_RECONNECT = 3;
let reconnectTimeout = null;

// Session timer
let sessionStartTime = null;
let sessionTimerInterval = null;

// Audio constants
const PLAYBACK_SAMPLE_RATE = 24000;
const MIC_SAMPLE_RATE = 16000;

// ========================================================================
// DOM References
// ========================================================================
const btnStart = document.getElementById("btnStart");
const btnStop = document.getElementById("btnStop");
const statusIndicator = document.getElementById("statusIndicator");
const statusText = document.getElementById("statusText");
const transcript = document.getElementById("transcript");
const emptyState = document.getElementById("emptyState");
const webcamVideo = document.getElementById("webcamVideo");
const captureCanvas = document.getElementById("captureCanvas");
const videoPlaceholder = document.getElementById("videoPlaceholder");
const bottomSheet = document.getElementById("bottomSheet");
const sheetHandle = document.getElementById("sheetHandle");
const sessionTimer = document.getElementById("sessionTimer");
const analysisOverlay = document.getElementById("analysisOverlay");
const analysisContent = document.getElementById("analysisContent");
const btnSnapshot = document.getElementById("btnSnapshot");

// ========================================================================
// Helpers
// ========================================================================
function setStatus(state, text) {
    statusIndicator.className = "status-pill " + state;
    statusText.textContent = text;
}

function haptic(pattern) {
    if (navigator.vibrate) {
        navigator.vibrate(pattern);
    }
}

function addMessage(role, text) {
    if (emptyState) emptyState.style.display = "none";
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.textContent = text;
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;

    // Auto-expand sheet when messages arrive
    if (!bottomSheet.classList.contains("expanded")) {
        bottomSheet.classList.add("expanded");
    }
}

function float32ToInt16(float32Arr) {
    const int16 = new Int16Array(float32Arr.length);
    for (let i = 0; i < float32Arr.length; i++) {
        let s = Math.max(-1, Math.min(1, float32Arr[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return int16;
}

function downsample(buffer, fromRate, toRate) {
    if (fromRate === toRate) return buffer;
    const ratio = fromRate / toRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
        const idx = i * ratio;
        const low = Math.floor(idx);
        const high = Math.min(low + 1, buffer.length - 1);
        const frac = idx - low;
        result[i] = buffer[low] * (1 - frac) + buffer[high] * frac;
    }
    return result;
}

function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer instanceof ArrayBuffer ? buffer : buffer.buffer);
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

function base64ToArrayBuffer(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
}

// ========================================================================
// Session Timer
// ========================================================================
function startSessionTimer() {
    sessionStartTime = Date.now();
    sessionTimer.classList.add("active");
    sessionTimerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - sessionStartTime) / 1000);
        const mins = String(Math.floor(elapsed / 60)).padStart(2, "0");
        const secs = String(elapsed % 60).padStart(2, "0");
        sessionTimer.textContent = mins + ":" + secs;
    }, 1000);
}

function stopSessionTimer() {
    if (sessionTimerInterval) {
        clearInterval(sessionTimerInterval);
        sessionTimerInterval = null;
    }
    sessionTimer.classList.remove("active");
    sessionTimer.textContent = "00:00";
}

// ========================================================================
// Audio Playback (24 kHz PCM from server with Jitter Buffer)
// ========================================================================
function playAudioChunk(pcmBase64) {
    if (!audioContext) return;

    const arrayBuf = base64ToArrayBuffer(pcmBase64);
    const int16 = new Int16Array(arrayBuf);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 32768;
    }

    // Push to jitter buffer
    audioQueue.push(float32);

    // Initial trigger: wait for 3 chunks
    if (!isPlaybackStarted && audioQueue.length >= JITTER_BUFFER_THRESHOLD) {
        isPlaybackStarted = true;
        nextPlayTime = audioContext.currentTime + 0.05; // Small initial offset
        scheduleNextBuffer();
    }

    // Pulse the mic button when AI speaks
    btnStart.classList.add("speaking");
}

function scheduleNextBuffer() {
    if (!isSessionActive || !audioContext || audioQueue.length === 0) {
        if (audioQueue.length === 0) {
            isPlaybackStarted = false;
            // We don't remove 'speaking' class here immediately to allow for small gaps
            setTimeout(() => {
                if (audioQueue.length === 0) btnStart.classList.remove("speaking");
            }, 200);
        }
        return;
    }

    const float32 = audioQueue.shift();
    const audioBuffer = audioContext.createBuffer(1, float32.length, PLAYBACK_SAMPLE_RATE);
    audioBuffer.getChannelData(0).set(float32);

    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);

    // Schedule seamlessly
    const now = audioContext.currentTime;
    if (nextPlayTime < now) {
        nextPlayTime = now + 0.01;
    }

    source.onended = () => {
        const index = activeAudioSources.indexOf(source);
        if (index > -1) activeAudioSources.splice(index, 1);
        scheduleNextBuffer(); // Chain the next buffer
    };

    activeAudioSources.push(source);
    source.start(nextPlayTime);
    nextPlayTime += audioBuffer.duration;
}

function flushAudioPlayback() {
    activeAudioSources.forEach(source => {
        try { source.stop(); } catch (e) { }
    });
    activeAudioSources = [];
    audioQueue = [];
    isPlaybackStarted = false;
    if (audioContext) {
        nextPlayTime = audioContext.currentTime;
    }
    btnStart.classList.remove("speaking");
}

// ========================================================================
// Microphone Capture (16 kHz, 16-bit PCM)
// ========================================================================
async function startMicrophone() {
    micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            sampleRate: { ideal: MIC_SAMPLE_RATE },
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
        },
    });

    const micAudioCtx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: MIC_SAMPLE_RATE,
    });
    await micAudioCtx.resume();

    const source = micAudioCtx.createMediaStreamSource(micStream);
    const actualRate = micAudioCtx.sampleRate;

    const bufferSize = 4096;
    micProcessor = micAudioCtx.createScriptProcessor(bufferSize, 1, 1);
    micProcessor._audioCtx = micAudioCtx;

    micProcessor.onaudioprocess = (event) => {
        if (!isSessionActive || !ws || ws.readyState !== WebSocket.OPEN) return;

        let inputData = event.inputBuffer.getChannelData(0);

        // FIX 1: ALWAYS downsample first so the array size is correct!
        if (actualRate !== MIC_SAMPLE_RATE) {
            inputData = downsample(inputData, actualRate, MIC_SAMPLE_RATE);
        }

        // FIX 2: Now that it is the correct size, mute it if the AI is speaking
        if (btnStart.classList.contains("speaking")) {
            inputData = new Float32Array(inputData.length); // Array of zeros
        }

        const pcm16 = float32ToInt16(inputData);
        const b64 = arrayBufferToBase64(pcm16);
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "audio", data: b64 }));
        }
    };

    source.connect(micProcessor);
    micProcessor.connect(micAudioCtx.destination);
}

function stopMicrophone() {
    if (micProcessor) {
        micProcessor.disconnect();
        if (micProcessor._audioCtx) {
            micProcessor._audioCtx.close().catch(() => { });
        }
        micProcessor = null;
    }
    if (micStream) {
        micStream.getTracks().forEach((t) => t.stop());
        micStream = null;
    }
}

// ========================================================================
// Webcam Capture (JPEG at 1 FPS)
// ========================================================================
async function startWebcam() {
    try {
        webcamStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: currentFacingMode, width: { ideal: 640 }, height: { ideal: 480 } },
        });
    } catch (err) {
        console.warn("Camera not available:", err.message);
        addMessage("system", "⚠️ Camera not available. Voice-only mode active.");
        return;
    }

    webcamVideo.srcObject = webcamStream;
    videoPlaceholder.style.display = "none";

    const ctx = captureCanvas.getContext("2d");
    let isCapturing = false;

    webcamInterval = setInterval(() => {
        if (!isSessionActive || !ws || ws.readyState !== WebSocket.OPEN || isCapturing) return;

        isCapturing = true;

        captureCanvas.width = webcamVideo.videoWidth || 640;
        captureCanvas.height = webcamVideo.videoHeight || 480;
        ctx.drawImage(webcamVideo, 0, 0, captureCanvas.width, captureCanvas.height);

        captureCanvas.toBlob(
            (blob) => {
                if (!blob) {
                    isCapturing = false;
                    return;
                }
                const reader = new FileReader();
                reader.onloadend = () => {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        const b64 = reader.result.split(",")[1];
                        ws.send(JSON.stringify({ type: "video", data: b64 }));
                    }
                    isCapturing = false;
                };
                reader.onerror = () => {
                    isCapturing = false;
                };
                reader.readAsDataURL(blob);
            },
            "image/jpeg",
            0.7
        );
    }, 1000);
}

function stopWebcam() {
    if (webcamInterval) {
        clearInterval(webcamInterval);
        webcamInterval = null;
    }
    if (webcamStream) {
        webcamStream.getTracks().forEach((t) => t.stop());
        webcamStream = null;
    }
    webcamVideo.srcObject = null;
    videoPlaceholder.style.display = "flex";
}

async function switchCamera() {
    if (!isSessionActive) return;
    haptic(15);
    currentFacingMode = currentFacingMode === "environment" ? "user" : "environment";
    stopWebcam();
    await startWebcam();
}

// ========================================================================
// Crop Analysis (REST endpoint)
// ========================================================================
async function takeSnapshot() {
    if (!isSessionActive || !webcamStream) {
        addMessage("system", "⚠️ Start a session first to analyze crops.");
        return;
    }

    haptic([30, 50, 30]);

    // Capture frame
    const ctx = captureCanvas.getContext("2d");
    captureCanvas.width = webcamVideo.videoWidth || 640;
    captureCanvas.height = webcamVideo.videoHeight || 480;
    ctx.drawImage(webcamVideo, 0, 0, captureCanvas.width, captureCanvas.height);

    // Pause video to show the captured frame
    webcamVideo.pause();

    // Show overlay with loading state
    analysisOverlay.classList.remove("hidden");
    analysisContent.innerHTML = `
        <div class="analysis-loading">
            <div class="spinner"></div>
            <span>Analyzing your crop…</span>
        </div>
    `;

    setStatus("analyzing", "Analyzing…");

    try {
        const blob = await new Promise((resolve) =>
            captureCanvas.toBlob(resolve, "image/jpeg", 0.85)
        );

        const reader = new FileReader();
        const b64 = await new Promise((resolve, reject) => {
            reader.onloadend = () => resolve(reader.result.split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });

        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image: b64 }),
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const result = await response.json();
        renderAnalysisResult(result);
        haptic(50);
    } catch (err) {
        console.error("Analysis failed:", err);
        haptic([100, 50, 100]);
        analysisContent.innerHTML = `
            <div style="text-align:center; padding: 16px 0; color: var(--danger);">
                <p style="font-weight: 600;">Analysis failed</p>
                <p style="font-size: 0.78rem; margin-top: 6px; color: var(--text-muted);">${err.message}</p>
            </div>
        `;
    } finally {
        if (isSessionActive) {
            setStatus("live", "Live");
        }
    }
}

function renderAnalysisResult(data) {
    // FIX: Intercept a null or undefined payload
    if (!data) {
        data = {
            species: "Unknown",
            disease: "Could not analyze image",
            confidence_score: 0,
            organic_remedies: []
        };
    }

    const confidence = data.confidence_score || 0;
    const disease = data.disease || "None detected";
    const species = data.species || "Unknown";
    const remedies = data.organic_remedies || [];

    let remediesHtml = "";
    if (remedies.length > 0) {
        remediesHtml = `
            <div class="analysis-field">
                <span class="analysis-field-label">Organic Remedies</span>
                <ul class="remedies-list">
                    ${remedies.map(r => `<li>${r}</li>`).join("")}
                </ul>
            </div>
        `;
    }

    analysisContent.innerHTML = `
        <div class="analysis-result">
            <div class="analysis-field">
                <span class="analysis-field-label">Identified Crop</span>
                <span class="analysis-field-value highlight">${species}</span>
            </div>
            <div class="analysis-field">
                <span class="analysis-field-label">Disease / Issue</span>
                <span class="analysis-field-value ${disease !== 'None detected' ? 'danger' : ''}">${disease}</span>
            </div>
            <div class="analysis-field">
                <span class="analysis-field-label">Confidence</span>
                <span class="analysis-field-value">${confidence}%</span>
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width: ${confidence}%"></div>
                </div>
            </div>
            ${remediesHtml}
        </div>
    `;
}

function closeAnalysis() {
    analysisOverlay.classList.add("hidden");
    if (isSessionActive && webcamStream) {
        webcamVideo.play().catch(() => { });
    }
}

// ========================================================================
// WebSocket Connection (with auto-reconnect)
// ========================================================================
function connectWebSocket() {
    return new Promise((resolve, reject) => {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${proto}//${location.host}/ws`;

        ws = new WebSocket(url);

        ws.onopen = () => {
            console.log("[WS] Connected");
            reconnectAttempts = 0;
            // NEW: Client-to-server heartbeat to keep proxies alive
            if (window._pingInterval) clearInterval(window._pingInterval);
            window._pingInterval = setInterval(() => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "ping" }));
                }
            }, 10000);
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);

                switch (msg.type) {
                    case "status":
                        if (msg.data === "connected") {
                            setStatus("live", "Live");
                            addMessage("system", "🌾 Connected to AgriBot. Speak or show your crop!");
                            btnStart.classList.add("listening");
                            resolve();
                        } else if (msg.data.startsWith("error")) {
                            setStatus("error", "Error");
                            addMessage("system", "❌ " + msg.data);
                            reject(new Error(msg.data));
                        }
                        break;

                    case "audio":
                        playAudioChunk(msg.data);
                        break;

                    case "text":
                        addMessage("bot", msg.data);
                        break;

                    case "interrupted":
                        console.log("[AgriBot] Interrupted by user.");
                        flushAudioPlayback();
                        break;

                    case "turn_complete":
                        console.log("[AgriBot] Turn complete.");
                        // FIX 2: Force playback to start if it was a short sentence
                        if (!isPlaybackStarted && audioQueue.length > 0) {
                            isPlaybackStarted = true;
                            nextPlayTime = audioContext.currentTime + 0.01;
                            scheduleNextBuffer();
                        }
                        break;

                    case "ping":
                        // Heartbeat — silently ignore
                        break;

                    default:
                        console.log("[WS] Unknown:", msg.type);
                }
            } catch (err) {
                console.error("[WS] Parse error:", err);
            }
        };

        ws.onclose = (event) => {
            console.log("[WS] Closed:", event.code, event.reason);
            btnStart.classList.remove("listening", "speaking");

            // FIX 3: Nuke the old audio queue so the next session starts fresh
            flushAudioPlayback();

            if (isSessionActive) {
                // Attempt auto-reconnect
                if (reconnectAttempts < MAX_RECONNECT) {
                    reconnectAttempts++;
                    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 8000);
                    setStatus("connecting", `Reconnecting (${reconnectAttempts}/${MAX_RECONNECT})…`);
                    addMessage("system", `⚡ Connection lost. Reconnecting in ${delay / 1000}s...`);

                    reconnectTimeout = setTimeout(async () => {
                        try {
                            // FIX 3: Turn off the dead hardware
                            stopMicrophone();
                            stopWebcam();

                            await connectWebSocket();

                            // FIX 4: Turn on fresh hardware for the new session
                            await startMicrophone();
                            await startWebcam();

                            addMessage("system", "✅ Reconnected to AgriBot!");
                        } catch (err) {
                            console.error("Reconnect failed:", err);
                        }
                    }, delay);
                } else {
                    setStatus("error", "Disconnected");
                    addMessage("system", "Connection lost. Please restart the session.");
                    cleanupSession();
                }
            }
        };

        ws.onerror = (err) => {
            console.error("[WS] Error:", err);
            reject(err);
        };
    });
}

// ========================================================================
// Suggestion Chips
// ========================================================================
function sendSuggestion(text) {
    if (!isSessionActive || !ws || ws.readyState !== WebSocket.OPEN) {
        addMessage("system", "⚠️ Start a session first.");
        return;
    }
    haptic(15);
    addMessage("user", text);
    ws.send(JSON.stringify({ type: "text", data: text }));
}

// ========================================================================
// Session Lifecycle
// ========================================================================
async function startSession() {
    btnStart.disabled = true;
    haptic(30);
    setStatus("connecting", "Connecting…");

    try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: PLAYBACK_SAMPLE_RATE,
        });
        await audioContext.resume();
        nextPlayTime = 0;

        await connectWebSocket();
        await startMicrophone();
        await startWebcam();

        isSessionActive = true;
        btnStop.disabled = false;
        startSessionTimer();
    } catch (err) {
        console.error("Failed to start session:", err);
        haptic([100, 50, 100]);
        setStatus("error", "Failed");
        addMessage("system", "❌ Could not start session: " + err.message);
        cleanupSession();
        btnStart.disabled = false;
    }
}

function stopSession() {
    haptic(20);
    addMessage("system", "Session ended.");
    cleanupSession();
}

function cleanupSession() {
    isSessionActive = false;
    stopMicrophone();
    stopWebcam();
    stopSessionTimer();

    if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
    }

    if (ws) {
        if (window._pingInterval) {
            clearInterval(window._pingInterval);
            window._pingInterval = null;
        }
        ws.close();
        ws = null;
    }
    if (audioContext) {
        audioContext.close().catch(() => { });
        audioContext = null;
    }

    btnStart.classList.remove("speaking", "listening");
    setStatus("", "Offline");
    btnStart.disabled = false;
    btnStop.disabled = true;
}

// ========================================================================
// Bottom Sheet — Touch Drag
// ========================================================================
(function initBottomSheet() {
    let startY = 0;
    let isDragging = false;

    sheetHandle.addEventListener("touchstart", (e) => {
        startY = e.touches[0].clientY;
        isDragging = true;
    }, { passive: true });

    sheetHandle.addEventListener("touchmove", (e) => {
        if (!isDragging) return;
        const dy = e.touches[0].clientY - startY;
        // Swipe up = expand, swipe down = collapse
        if (dy < -30) {
            bottomSheet.classList.add("expanded");
            isDragging = false;
        } else if (dy > 30) {
            bottomSheet.classList.remove("expanded");
            isDragging = false;
        }
    }, { passive: true });

    sheetHandle.addEventListener("touchend", () => {
        isDragging = false;
    }, { passive: true });

    // Click toggle for desktop
    sheetHandle.addEventListener("click", () => {
        bottomSheet.classList.toggle("expanded");
    });
})();