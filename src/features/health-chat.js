import { showToast } from '../ui.js';

// ── State ────────────────────────────────────────────────────────────────────
let socket = null;
let audioContext = null;
let audioWorklet = null;
let mediaStream = null;
let sessionId = localStorage.getItem('medisense_session_id') || `session-${Date.now()}`;
localStorage.setItem('medisense_session_id', sessionId);

let isRecording = false;
let isConnected = false;
let isConnecting = false;
let sessionStartPending = false;

let audioQueue = [];
let isPlayingAudio = false;
let playbackAudioContext = null;

let cameraStream = null;
let screenStream = null;
let cameraInterval = null;
let screenInterval = null;
let isProcessingFrame = false;

let uploadedImages = [];
let currentUserName = 'Rama';
let notesCount = 0;
let sessionTimerInterval = null;
let sessionStartTime = null;
let currentPatientData = null;

// ── DOM refs (populated after template mount) ────────────────────────────────
let messagesContainer, userInput, sendButton, voiceButton, uploadButton;
let cameraButton, screenShareButton, cameraVideo, cameraContainer;
let startSessionBtn, stopSessionBtn, clearSessionBtn;
let voiceActivity, aiThinking;
let userNameInput, setNameBtn, currentUserNameDisplay;
let urgentBanner, urgentText, urgentAction, dismissAlertBtn;
let clinicalNotesList, notesCountEl, exportNotesBtn;
let uploadPreviewContainer, uploadPreviewGrid, uploadCountEl, removeUploadBtn;

// ── Exported entry points ────────────────────────────────────────────────────

export async function getHealthChatContent() {
    try {
        const res = await fetch('/src/features/templates/health-chat.html');
        if (!res.ok) throw new Error(`Template fetch failed: ${res.status}`);
        return await res.text();
    } catch (err) {
        console.error('Template load error:', err);
        return '<div class="text-red-500 p-6">Failed to load chat interface. Please refresh.</div>';
    }
}

export function initHealthChat() {
    setTimeout(() => {
        messagesContainer    = document.getElementById('messages-container');
        userInput            = document.getElementById('user-input');
        sendButton           = document.getElementById('send-button');
        voiceButton          = document.getElementById('voice-button');
        uploadButton         = document.getElementById('upload-button');
        cameraButton         = document.getElementById('camera-button');
        screenShareButton    = document.getElementById('screen-share-button');
        cameraVideo          = document.getElementById('camera-video');
        cameraContainer      = document.getElementById('camera-container');
        startSessionBtn      = document.getElementById('start-session-btn');
        stopSessionBtn       = document.getElementById('stop-session-btn');
        clearSessionBtn      = document.getElementById('clear-session-btn');
        voiceActivity        = document.getElementById('voice-activity');
        aiThinking           = document.getElementById('ai-thinking');
        userNameInput        = document.getElementById('user-name-input');
        setNameBtn           = document.getElementById('set-name-btn');
        currentUserNameDisplay = document.getElementById('current-user-name');
        urgentBanner         = document.getElementById('urgent-alert-banner');
        urgentText           = document.getElementById('urgent-alert-text');
        urgentAction         = document.getElementById('urgent-alert-action');
        dismissAlertBtn      = document.getElementById('dismiss-alert-btn');
        clinicalNotesList    = document.getElementById('clinical-notes-list');
        notesCountEl         = document.getElementById('notes-count');
        exportNotesBtn       = document.getElementById('export-notes-btn');
        uploadPreviewContainer = document.getElementById('upload-preview-container');
        uploadPreviewGrid    = document.getElementById('upload-preview-grid');
        uploadCountEl        = document.getElementById('upload-count');
        removeUploadBtn      = document.getElementById('remove-upload-btn');

        if (!messagesContainer || !userInput || !sendButton) {
            console.error('Required UI elements not found');
            return;
        }

        initSocket();
        loadUserName();
        renderUploadPreview();    // ensure upload container is hidden initially
        applyModeToChat(localStorage.getItem('medisense_mode') || 'nurse');

        // Listen for mode changes triggered from the sidebar — reset session on switch
        window.addEventListener('medisense-mode-change', e => {
            resetSessionOnModeSwitch();
            applyModeToChat(e.detail.mode);
        });

        // Listen for patient record loads from the sidebar
        window.addEventListener('medisense-patient-loaded', e => {
            const patient = e.detail?.patient;
            currentPatientData = patient || null;
            updateVitalsStrip(patient || null);
            renderRiskScores(patient || null);
            if (patient) {
                addSystemMessage(
                    `📋 <strong>Patient record loaded:</strong> ${patient.name} ` +
                    `(${patient.age}${patient.gender[0]}, ${patient.blood_type}) — ` +
                    `<em>${patient.chief_complaint}</em>. ` +
                    (patient.allergies?.length ? `<span class="text-red-500 font-semibold">⚠️ Allergies: ${patient.allergies.join(', ')}</span>. ` : '') +
                    `The AI is now context-aware of this patient's full medical record.`
                );
            } else {
                addSystemMessage('🔄 Patient record cleared. No active patient context.');
            }
        });

        sendButton.addEventListener('click', sendMessage);
        userInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendMessage(); });
        voiceButton.addEventListener('click', toggleVoice);
        uploadButton.addEventListener('click', () => document.getElementById('image-upload-input').click());
        document.getElementById('image-upload-input').addEventListener('change', handleImageUpload);
        cameraButton.addEventListener('click', toggleCamera);
        screenShareButton.addEventListener('click', toggleScreenShare);
        startSessionBtn.addEventListener('click', handleStartSession);
        stopSessionBtn.addEventListener('click', handleStopSession);
        clearSessionBtn.addEventListener('click', clearSession);
        dismissAlertBtn?.addEventListener('click', () => { urgentBanner.classList.add('hidden'); });
        removeUploadBtn?.addEventListener('click', clearUploadedImages);
        setNameBtn?.addEventListener('click', setUserName);
        userNameInput?.addEventListener('keypress', e => { if (e.key === 'Enter') setUserName(); });
        exportNotesBtn?.addEventListener('click', exportClinicalLog);

        // Smart clinical features
        document.getElementById('generate-ddx-btn')?.addEventListener('click', generateDDx);
        document.getElementById('generate-sbar-btn')?.addEventListener('click', generateSBAR);
        document.getElementById('generate-image-btn')?.addEventListener('click', generateVisualAid);
        document.getElementById('close-ddx-btn')?.addEventListener('click', () => {
            document.getElementById('ddx-panel')?.classList.add('hidden');
        });
        document.getElementById('close-sbar-btn')?.addEventListener('click', () => {
            document.getElementById('sbar-modal')?.classList.add('hidden');
        });
        document.getElementById('copy-sbar-btn')?.addEventListener('click', () => {
            const content = document.getElementById('sbar-content');
            const raw = content?.dataset.rawText;
            if (raw) {
                navigator.clipboard.writeText(raw).then(() => {
                    const btn = document.getElementById('copy-sbar-btn');
                    if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 2000); }
                });
            }
        });

        // Procedure checklist
        document.getElementById('stop-procedure-btn')?.addEventListener('click', stopProcedure);
        loadProcedureLibrary();

        // Resume audio context on user interaction
        document.addEventListener('click', () => {
            if (playbackAudioContext?.state === 'suspended') playbackAudioContext.resume();
        }, { once: true });

    }, 100);
}

// ── Auth / Name ──────────────────────────────────────────────────────────────

async function loadUserName() {
    try {
        const res = await fetch('/api/get-user-name');
        const data = await res.json();
        if (data.name && currentUserNameDisplay) {
            currentUserNameDisplay.textContent = data.name;
            currentUserName = data.name;
        }
    } catch (_) {}
}

async function setUserName() {
    const name = userNameInput?.value.trim();
    if (!name) { showToast('Enter a name', 'warning'); return; }
    try {
        const res = await fetch('/api/set-user-name', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        const data = await res.json();
        if (data.success) {
            currentUserName = data.name;
            if (currentUserNameDisplay) currentUserNameDisplay.textContent = data.name;
            showToast(`Welcome, ${data.name}!`, 'success');
            userNameInput.value = '';
        }
    } catch (_) { showToast('Failed to set name', 'error'); }
}

// ── Socket setup ─────────────────────────────────────────────────────────────

function initSocket() {
    if (socket?.connected) return;
    if (socket) socket.close();

    socket = io({
        transports: ['polling'],
        upgrade: false,
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 3000,
        reconnectionAttempts: Infinity,
        timeout: 60000,
    });

    socket.on('connect', () => {
        updateStatus('Ready', 'success');
        socket.emit('check_session_status', { session_id: sessionId }, res => {
            if (res?.active) {
                isConnected = true;
                updateStatus('Session Active', 'active');
                setSessionButtons(true);
            }
        });
    });

    socket.on('disconnect', reason => {
        if (['io server disconnect', 'io client disconnect'].includes(reason)) {
            updateStatus('Disconnected', 'error');
            isConnected = false;
            setSessionButtons(false);
        }
        isConnecting = false;
        sessionStartPending = false;
    });

    socket.on('live_session_started', data => {
        isConnected = true;
        isConnecting = false;
        sessionStartPending = false;
        updateStatus('Session Active', 'active');
        setSessionButtons(true);
        startSessionTimer();
        if (data?.user_name) {
            currentUserName = data.user_name;
            if (currentUserNameDisplay) currentUserNameDisplay.textContent = data.user_name;
        }
        addSystemMessage('✅ MediSense connected. Share your camera or screen, then ask your clinical question.');
    });

    socket.on('live_session_stopped', () => {
        isConnected = false;
        stopSessionTimer();
        updateStatus('Ready', 'success');
        setSessionButtons(false);
        addSystemMessage('Session ended.');
    });

    socket.on('session_ended_reconnect', data => {
        isConnected = false;
        if (cameraStream || screenStream || isRecording) {
            if (!isConnecting) {
                isConnecting = true;
                updateStatus('Reconnecting...', 'connecting');
                setTimeout(() => { requestSessionStart(); isConnecting = false; }, 800);
            }
        } else {
            updateStatus('Ready', 'success');
            setSessionButtons(false);
        }
    });

    socket.on('live_session_error', data => {
        showToast('Session error: ' + data.error, 'error', 6000);
        isConnecting = false;
        sessionStartPending = false;
        setSessionButtons(false);
    });

    socket.on('text_response', data => {
        if (data.text) {
            aiThinking?.classList.add('hidden');
            addMessage('assistant', data.text);
        }
    });

    socket.on('audio_response', playAudioResponse);

    socket.on('image_response', data => {
        if (data.image) {
            aiThinking?.classList.add('hidden');
            addImageMessage(data.image, data.mime_type || 'image/png', data.context || '');
        }
    });

    // Tool calls forwarded to backend for execution
    socket.on('tool_call', async data => {
        const { function_name, function_args, function_call_id } = data;
        try {
            const res = await fetch('/api/tool-call', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId, function_name, function_args, function_call_id })
            });
            if (!res.ok) console.error('Tool call HTTP error', res.status);
        } catch (err) {
            console.error('Tool call failed:', err);
        }
    });

    // Clinical note added by the model
    socket.on('clinical_note_added', entry => {
        appendClinicalNote(entry);
    });

    // Urgent alert raised by the model
    socket.on('urgent_alert', entry => {
        appendClinicalNote(entry);
        showUrgentAlert(entry.alert, entry.action_required);
        showToast('⚠️ URGENT alert raised!', 'error', 8000);
    });

    // Procedure step verified/flagged by the model
    socket.on('procedure_step_update', data => {
        updateProcedureStepUI(data.step_number, data.status, data.observation);
    });

    socket.on('reconnect', () => {
        updateStatus('Ready', 'success');
        socket.emit('check_session_status', { session_id: sessionId }, res => {
            if (res?.active) { isConnected = true; updateStatus('Session Active', 'active'); setSessionButtons(true); }
        });
    });
}

// ── Session control ───────────────────────────────────────────────────────────

function requestSessionStart() {
    if (sessionStartPending) return false;
    sessionStartPending = true;
    socket.emit('start_live_session', { session_id: sessionId });
    return true;
}

async function handleStartSession() {
    if (isConnected || isConnecting) return;
    isConnecting = true;
    updateStatus('Connecting...', 'connecting');
    showToast('Starting session...', 'info');
    requestSessionStart();

    const connected = await waitForConnection(30000);
    if (!connected) {
        isConnecting = false;
        sessionStartPending = false;
        updateStatus('Failed', 'error');
        showToast('Connection timeout. Please try authenticating first.', 'error', 6000);
        return;
    }

    // Auto-start mic once connected
    if (!isRecording) {
        setTimeout(() => startVoice(), 300);
    }
}

function handleStopSession() {
    if (!isConnected) return;
    if (isRecording) stopVoice();
    if (cameraStream) stopCamera();
    if (screenStream) stopScreenShare();
    stopSessionTimer();
    socket.emit('stop_live_session', { session_id: sessionId });
    isConnected = false;
    updateStatus('Ready', 'success');
    setSessionButtons(false);
    showToast('Session ended', 'info');
}

async function clearSession() {
    if (!confirm('Clear all messages and clinical notes for this session?')) return;

    if (isConnected) handleStopSession();

    await fetch('/api/clear-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId })
    }).catch(() => {});

    sessionId = `session-${Date.now()}`;
    localStorage.setItem('medisense_session_id', sessionId);

    if (messagesContainer) {
        messagesContainer.innerHTML = `
            <div class="text-center text-gray-400 py-10">
                <p class="text-3xl mb-2">🏥</p>
                <p class="text-sm">Session cleared. Ready to start.</p>
            </div>`;
    }

    clearUploadedImages();
    notesCount = 0;
    if (notesCountEl) notesCountEl.textContent = '0';
    if (clinicalNotesList) {
        clinicalNotesList.innerHTML = '<p class="text-xs text-gray-400 text-center py-6 italic">Clinical observations will appear here.</p>';
    }
    urgentBanner?.classList.add('hidden');

    showToast('Session cleared', 'success');
}

async function resetSessionOnModeSwitch() {
    // Stop any active session
    if (isConnected) handleStopSession();

    // Clear backend session state
    await fetch('/api/clear-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId })
    }).catch(() => {});

    // Stop active procedure if any
    if (activeProcedureData) {
        await fetch('/api/stop-procedure', { method: 'POST' }).catch(() => {});
        activeProcedureData = null;
        renderProcedureSteps();
    }

    // Generate fresh session ID
    sessionId = `session-${Date.now()}`;
    localStorage.setItem('medisense_session_id', sessionId);

    // Reset chat UI
    if (messagesContainer) {
        messagesContainer.innerHTML = '';
    }
    clearUploadedImages();
    notesCount = 0;
    if (notesCountEl) notesCountEl.textContent = '0';
    if (clinicalNotesList) {
        clinicalNotesList.innerHTML = '<p class="text-xs text-gray-400 text-center py-6 italic">Clinical observations will appear here.</p>';
    }
    urgentBanner?.classList.add('hidden');
    currentPatientData = null;
    updateVitalsStrip(null);
    renderRiskScores(null);
}

function waitForConnection(timeout = 15000) {
    return new Promise(resolve => {
        if (isConnected) { resolve(true); return; }
        const start = Date.now();
        const timer = setInterval(() => {
            if (isConnected) { clearInterval(timer); resolve(true); }
            else if (Date.now() - start > timeout) { clearInterval(timer); resolve(false); }
        }, 100);
    });
}

// ── Messaging ─────────────────────────────────────────────────────────────────

async function sendMessage() {
    if (!userInput) return;
    const text = userInput.value.trim();
    if (!text) return;

    if (isConnecting) { showToast('Please wait, connecting...', 'warning'); return; }

    addMessage('user', text);
    userInput.value = '';

    if (!isConnected && !isConnecting) {
        isConnecting = true;
        updateStatus('Connecting...', 'connecting');
        requestSessionStart();
        const connected = await waitForConnection(30000);
        isConnecting = false;
        if (!connected) {
            sessionStartPending = false;
            showToast('Connection failed. Please authenticate first.', 'error');
            return;
        }
        await new Promise(r => setTimeout(r, 500));
    }

    if (isConnected) {
        aiThinking?.classList.remove('hidden');
        if (uploadedImages.length > 0) {
            socket.emit('send_message_with_images', { session_id: sessionId, text, images: uploadedImages });
        } else {
            socket.emit('send_text_message', { session_id: sessionId, text });
        }
    } else {
        showToast('Not connected. Please start a session.', 'error');
    }
}

// ── Voice ─────────────────────────────────────────────────────────────────────

async function toggleVoice() {
    if (!isRecording) await startVoice();
    else stopVoice();
}

async function startVoice() {
    try {
        if (isConnecting) return;

        if (!playbackAudioContext) {
            playbackAudioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
            if (playbackAudioContext.state === 'suspended') await playbackAudioContext.resume();
        }

        if (!isConnected && !isConnecting) {
            isConnecting = true;
            updateStatus('Connecting...', 'connecting');
            requestSessionStart();
            const connected = await waitForConnection(30000);
            if (!connected) {
                isConnecting = false;
                sessionStartPending = false;
                showToast('Connection timeout.', 'error');
                return;
            }
            isConnecting = false;
        }

        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                sampleRate: 16000,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            }
        });

        audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const source = audioContext.createMediaStreamSource(mediaStream);
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        let audioBuffer = [];
        let lastSend = Date.now();
        const INTERVAL = 500;

        processor.onaudioprocess = e => {
            if (!isRecording || !isConnected) return;
            const raw = e.inputBuffer.getChannelData(0);
            const int16 = new Int16Array(raw.length);
            for (let i = 0; i < raw.length; i++) {
                const s = Math.max(-1, Math.min(1, raw[i]));
                int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            audioBuffer.push(...Array.from(int16));
            const now = Date.now();
            if (now - lastSend >= INTERVAL && audioBuffer.length > 0) {
                socket.emit('send_audio', { session_id: sessionId, audio: audioBuffer });
                audioBuffer = [];
                lastSend = now;
            }
        };

        source.connect(processor);
        processor.connect(audioContext.destination);
        audioWorklet = processor;

        isRecording = true;
        voiceButton.classList.add('btn-active');
        voiceButton.querySelector('span')?.remove();
        const span = document.createElement('span');
        span.className = 'sr-only';
        span.textContent = 'Stop';
        voiceButton.appendChild(span);
        voiceActivity?.classList.remove('hidden');
        showToast('Listening...', 'success', 2000);
    } catch (err) {
        console.error('Voice error:', err);
        showToast('Microphone access failed', 'error');
        isConnecting = false;
    }
}

function stopVoice() {
    if (audioWorklet) { audioWorklet.disconnect(); audioWorklet = null; }
    if (audioContext) { audioContext.close(); audioContext = null; }
    if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
    isRecording = false;
    voiceButton?.classList.remove('btn-active');
if (voiceButton) voiceButton.querySelector('span')?.remove();
        if (voiceButton) {
            const span = document.createElement('span');
            span.className = 'sr-only';
            span.textContent = 'Voice';
            voiceButton.appendChild(span);
        }
    voiceActivity?.classList.add('hidden');
    showToast('Mic off', 'info', 1500);
}

// ── Camera ────────────────────────────────────────────────────────────────────

async function toggleCamera() {
    if (!cameraStream) await startCamera();
    else stopCamera();
}

async function startCamera() {
    try {
        if (!isConnected && !isConnecting) {
            isConnecting = true;
            updateStatus('Connecting...', 'connecting');
            requestSessionStart();
            await waitForConnection(30000);
            isConnecting = false;
        }
        cameraStream = await navigator.mediaDevices.getUserMedia({ video: { width: 768, height: 768 } });
        cameraVideo.srcObject = cameraStream;
        cameraContainer.classList.remove('hidden');
        cameraButton.classList.add('btn-active');
        cameraInterval = setInterval(sendCameraFrame, 2000);
        showToast('Camera on — MediSense can see the feed', 'success');
    } catch (err) {
        console.error('Camera error:', err);
        showToast('Camera access failed', 'error');
    }
}

function stopCamera() {
    if (cameraInterval) { clearInterval(cameraInterval); cameraInterval = null; }
    if (cameraStream) { cameraStream.getTracks().forEach(t => t.stop()); cameraStream = null; }
    if (cameraVideo) cameraVideo.srcObject = null;
    cameraContainer?.classList.add('hidden');
    cameraButton?.classList.remove('btn-active');
    showToast('Camera off', 'info', 1500);
}

function sendCameraFrame() {
    if (!cameraVideo || !cameraStream || !isConnected || isProcessingFrame) return;
    isProcessingFrame = true;
    const canvas = document.createElement('canvas');
    canvas.width = 768; canvas.height = 768;
    const ctx = canvas.getContext('2d');
    const ar = cameraVideo.videoWidth / cameraVideo.videoHeight;
    let dw = 768, dh = 768, ox = 0, oy = 0;
    if (ar > 1) { dh = 768 / ar; oy = (768 - dh) / 2; }
    else         { dw = 768 * ar; ox = (768 - dw) / 2; }
    ctx.drawImage(cameraVideo, ox, oy, dw, dh);
    canvas.toBlob(blob => {
        const reader = new FileReader();
        reader.onloadend = () => {
            const b64 = reader.result.split(',')[1];
            socket.emit('send_camera_frame', { session_id: sessionId, frame: b64 });
            setTimeout(() => { isProcessingFrame = false; }, 50);
        };
        reader.readAsDataURL(blob);
    }, 'image/jpeg', 0.6);
}

// ── Screen share ──────────────────────────────────────────────────────────────

async function toggleScreenShare() {
    if (!screenStream) await startScreenShare();
    else stopScreenShare();
}

async function startScreenShare() {
    try {
        if (!isConnected && !isConnecting) {
            isConnecting = true;
            updateStatus('Connecting...', 'connecting');
            requestSessionStart();
            await waitForConnection(30000);
            isConnecting = false;
        }
        screenStream = await navigator.mediaDevices.getDisplayMedia({ video: { width: 768, height: 768 } });
        const vid = document.createElement('video');
        vid.srcObject = screenStream;
        vid.autoplay = true;
        vid.style.display = 'none';
        document.body.appendChild(vid);
        screenShareButton.classList.add('btn-active');
        screenInterval = setInterval(() => sendScreenFrame(vid), 2000);
        screenStream.getVideoTracks()[0].addEventListener('ended', stopScreenShare);
        showToast('Screen sharing — MediSense can see your screen', 'success');
    } catch (err) {
        console.error('Screen share error:', err);
        showToast('Screen share failed', 'error');
    }
}

function stopScreenShare() {
    if (screenInterval) { clearInterval(screenInterval); screenInterval = null; }
    if (screenStream) { screenStream.getTracks().forEach(t => t.stop()); screenStream = null; }
    screenShareButton?.classList.remove('btn-active');
    showToast('Screen sharing stopped', 'info', 1500);
}

function sendScreenFrame(vid) {
    if (!vid || !screenStream || !isConnected || isProcessingFrame) return;
    isProcessingFrame = true;
    const canvas = document.createElement('canvas');
    canvas.width = 768; canvas.height = 768;
    const ctx = canvas.getContext('2d');
    const ar = vid.videoWidth / vid.videoHeight;
    let dw = 768, dh = 768, ox = 0, oy = 0;
    if (ar > 1) { dh = 768 / ar; oy = (768 - dh) / 2; }
    else         { dw = 768 * ar; ox = (768 - dw) / 2; }
    ctx.drawImage(vid, ox, oy, dw, dh);
    canvas.toBlob(blob => {
        const reader = new FileReader();
        reader.onloadend = () => {
            const b64 = reader.result.split(',')[1];
            socket.emit('send_camera_frame', { session_id: sessionId, frame: b64 });
            setTimeout(() => { isProcessingFrame = false; }, 50);
        };
        reader.readAsDataURL(blob);
    }, 'image/jpeg', 0.6);
}

// ── Image upload ──────────────────────────────────────────────────────────────

async function handleImageUpload(event) {
    const files = Array.from(event.target.files);
    if (!files.length) return;
    if (uploadedImages.length + files.length > 10) {
        showToast('Max 10 images. Clear some first.', 'warning');
        return;
    }
    for (const file of files) {
        if (!file.type.startsWith('image/')) {
            showToast('Only image files supported', 'error');
            return;
        }
    }
    for (const file of files) {
        const b64 = await new Promise(res => {
            const reader = new FileReader();
            reader.onload = e => res(e.target.result);
            reader.readAsDataURL(file);
        });
        uploadedImages.push(b64);
    }
    renderUploadPreview();
    showToast(`${uploadedImages.length} image(s) attached`, 'success');
}

function renderUploadPreview() {
    if (!uploadedImages.length) {
        uploadPreviewContainer.style.display = 'none';
        return;
    }
    if (uploadCountEl) uploadCountEl.textContent = uploadedImages.length;
    uploadPreviewGrid.innerHTML = '';
    uploadedImages.forEach((img, i) => {
        const div = document.createElement('div');
        div.className = 'relative group';
        div.innerHTML = `
            <img src="${img}" alt="Image ${i + 1}" class="w-full h-16 object-cover rounded border border-teal-300">
            <button onclick="window._removeUploadAt(${i})"
                class="absolute top-0.5 right-0.5 bg-red-500 text-white rounded-full w-4 h-4 text-xs font-bold opacity-0 group-hover:opacity-100 flex items-center justify-center">×</button>
        `;
        uploadPreviewGrid.appendChild(div);
    });
    uploadPreviewContainer.style.display = 'block';
}

window._removeUploadAt = index => {
    uploadedImages.splice(index, 1);
    renderUploadPreview();
    document.getElementById('image-upload-input').value = '';
};

function clearUploadedImages() {
    uploadedImages = [];
    renderUploadPreview();
    document.getElementById('image-upload-input').value = '';
}

// ── Audio playback ────────────────────────────────────────────────────────────

function playAudioResponse(data) {
    if (!data.audio) return;
    audioQueue.push(data);
    if (!isPlayingAudio) playNextAudio();
}

async function playNextAudio() {
    if (!audioQueue.length) { isPlayingAudio = false; return; }
    isPlayingAudio = true;
    const data = audioQueue.shift();
    try {
        if (!playbackAudioContext) {
            playbackAudioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
        }
        if (playbackAudioContext.state === 'suspended') await playbackAudioContext.resume();

        const bytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0));
        const int16 = new Int16Array(bytes.buffer);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            float32[i] = int16[i] / (int16[i] < 0 ? 32768 : 32767);
        }
        const buf = playbackAudioContext.createBuffer(1, float32.length, 24000);
        buf.getChannelData(0).set(float32);
        const src = playbackAudioContext.createBufferSource();
        src.buffer = buf;
        src.connect(playbackAudioContext.destination);
        src.onended = () => playNextAudio();
        src.start(0);
    } catch (err) {
        console.error('Playback error:', err);
        playNextAudio();
    }
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function updateStatus(text, type) {
    const el = document.getElementById('connection-status');
    const header = document.getElementById('header-status');
    const headerText = document.getElementById('header-status-text');

    const dotClass = {
        success:    'status-ready',
        active:     'status-active',
        connecting: 'status-connecting',
        error:      'status-error',
        inactive:   'status-inactive',
    }[type] || 'status-inactive';

    if (el) el.innerHTML = `<span class="status-dot ${dotClass}"></span><span>${text}</span>`;
    if (header) {
        header.classList.remove('hidden');
        headerText.textContent = text;
        const dot = header.querySelector('.status-dot');
        if (dot) dot.className = `status-dot ${dotClass}`;
    }
}

function setSessionButtons(active) {
    if (active) {
        startSessionBtn?.classList.add('hidden');
        stopSessionBtn?.classList.remove('hidden');
    } else {
        startSessionBtn?.classList.remove('hidden');
        stopSessionBtn?.classList.add('hidden');
    }
}

function addMessage(role, text) {
    if (!messagesContainer) return;
    const welcome = document.getElementById('welcome-message');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = `message message-${role} mb-1`;

    const label = role === 'user'
        ? `<div class="msg-label text-teal-600 dark:text-teal-400">${escapeHtml(currentUserName)}</div>`
        : role === 'assistant'
            ? '<div class="msg-label text-cyan-600 dark:text-cyan-400">🏥 MediSense</div>'
            : '';

    if (role === 'assistant') {
        div.innerHTML = `${label}<div class="bubble-wrap"><div class="bubble">${escapeHtml(text)}</div><button class="copy-btn">Copy</button></div>`;
        const copyBtn = div.querySelector('.copy-btn');
        copyBtn?.addEventListener('click', () => {
            navigator.clipboard.writeText(text).catch(() => {});
            copyBtn.classList.add('copied');
            copyBtn.textContent = '\u2713';
            setTimeout(() => { copyBtn.classList.remove('copied'); copyBtn.textContent = 'Copy'; }, 2000);
        });
    } else {
        div.innerHTML = `${label}<div class="bubble">${escapeHtml(text)}</div>`;
    }
    messagesContainer.appendChild(div);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    aiThinking?.classList.add('hidden');
}

function addImageMessage(imageB64, mimeType, context) {
    if (!messagesContainer) return;
    const welcome = document.getElementById('welcome-message');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = 'message message-assistant mb-1';

    const dataUrl = `data:${mimeType};base64,${imageB64}`;
    div.innerHTML = `
        <div class="msg-label text-cyan-600 dark:text-cyan-400">🏥 MediSense — Visual Aid</div>
        <div class="bubble-wrap">
            <div class="bubble" style="padding:8px">
                ${context ? `<p class="text-xs text-gray-500 dark:text-gray-400 mb-2">${escapeHtml(context)}</p>` : ''}
                <img src="${dataUrl}" alt="AI Generated Medical Illustration" 
                     class="rounded-lg max-w-full cursor-pointer" 
                     style="max-height:400px; border:1px solid rgba(0,0,0,0.1)"
                     onclick="window.open(this.src, '_blank')" />
            </div>
            <button class="copy-btn download-img-btn">Save</button>
        </div>`;

    const saveBtn = div.querySelector('.download-img-btn');
    saveBtn?.addEventListener('click', () => {
        const a = document.createElement('a');
        a.href = dataUrl;
        a.download = `medisense-visual-${Date.now()}.png`;
        a.click();
        saveBtn.textContent = '\u2713';
        setTimeout(() => { saveBtn.textContent = 'Save'; }, 2000);
    });

    messagesContainer.appendChild(div);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    aiThinking?.classList.add('hidden');
}

function showUrgentAlert(alert, action) {
    if (!urgentBanner) return;
    if (urgentText) urgentText.textContent = alert;
    if (urgentAction && action) urgentAction.textContent = `Action required: ${action}`;
    else if (urgentAction) urgentAction.textContent = '';
    urgentBanner.classList.remove('hidden');
    urgentBanner.classList.add('urgent-pulse');
    setTimeout(() => urgentBanner.classList.remove('urgent-pulse'), 3000);
}

function appendClinicalNote(entry) {
    if (!clinicalNotesList) return;

    // Remove placeholder
    const placeholder = clinicalNotesList.querySelector('p.text-gray-400');
    if (placeholder) placeholder.remove();

    notesCount++;
    if (notesCountEl) notesCountEl.textContent = notesCount;

    const div = document.createElement('div');
    const severity = entry.severity || 'info';
    div.className = `note-card ${severity}`;

    const time = new Date(entry.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const icon = severity === 'urgent' ? '🚨' : severity === 'warning' ? '⚠️' : '📝';
    const noteText = entry.type === 'urgent' ? entry.alert : entry.note;
    const action = entry.action_required ? `<p class="text-xs mt-1 opacity-75">→ ${escapeHtml(entry.action_required)}</p>` : '';

    div.innerHTML = `
        <div class="flex justify-between items-start mb-0.5">
            <span class="font-semibold">${icon} ${severity.toUpperCase()}</span>
            <span class="text-gray-400 text-xs flex-shrink-0 ml-1">${time}</span>
        </div>
        <p class="leading-tight">${escapeHtml(noteText)}</p>
        ${action}
    `;
    clinicalNotesList.prepend(div);
}

function exportClinicalLog() {
    const notes = Array.from(clinicalNotesList.querySelectorAll('.note-card'));
    if (!notes.length) { showToast('No notes to export', 'warning'); return; }

    let text = `MediSense Clinical Log — Session ${sessionId}\n`;
    text += `Exported: ${new Date().toLocaleString()}\n`;
    text += '='.repeat(60) + '\n\n';

    // Notes are prepended so reverse for chronological order
    [...notes].reverse().forEach(note => {
        text += note.innerText.trim() + '\n---\n';
    });

    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `medisense-log-${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('Clinical log exported', 'success');
}

function applyModeToChat(mode) {
    const isPatient = mode === 'patient';

    // Welcome message
    const icon  = document.getElementById('welcome-icon');
    const title = document.getElementById('welcome-title');
    const sub1  = document.getElementById('welcome-sub1');
    const sub2  = document.getElementById('welcome-sub2');
    if (icon)  icon.textContent  = isPatient ? '💊' : '🏥';
    if (title) title.textContent = isPatient ? 'Medicine Assistant ready.' : 'MediSense is ready.';
    if (sub1)  sub1.innerHTML    = isPatient
        ? 'Click <strong>▶ Start Session</strong> then show or upload your medicine.'
        : 'Click <strong>▶ Start Session</strong> then speak or type your clinical query.';
    if (sub2) {
        sub2.textContent = isPatient
            ? 'Show a pill, bottle, blister pack, or prescription via camera or upload.'
            : 'Share your camera or screen for real-time visual analysis.';
        sub2.className = isPatient ? 'text-xs mt-1 text-indigo-400' : 'text-xs mt-1 text-teal-500';
    }

    // Input placeholder
    const input = document.getElementById('user-input');
    if (input) {
        input.placeholder = isPatient
            ? 'e.g. "What is this medicine?" or "What are the side effects?"'
            : 'Describe symptoms, ask for guidance, or type a query...';
    }

    // Camera button tooltip
    const camBtn = document.getElementById('camera-button');
    if (camBtn) camBtn.title = isPatient ? 'Show medicine via camera' : 'Toggle camera';

    const uploadBtn = document.getElementById('upload-button');
    if (uploadBtn) uploadBtn.title = isPatient ? 'Upload photo of medicine or prescription' : 'Upload image (X-ray, lab, report)';

    // Thinking indicator text
    const thinkingText = document.getElementById('ai-thinking-text');
    if (thinkingText) thinkingText.textContent = isPatient ? 'Identifying medicine...' : 'MediSense is analyzing...';

    renderQuickChips(mode);
}

function addSystemMessage(htmlContent) {
    if (!messagesContainer) return;
    const welcome = document.getElementById('welcome-message');
    if (welcome) welcome.remove();
    const div = document.createElement('div');
    div.className = 'my-2 px-3 py-2.5 rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700/50 text-xs text-blue-700 dark:text-blue-300 leading-relaxed';
    div.innerHTML = htmlContent;
    messagesContainer.appendChild(div);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// ── Session timer ─────────────────────────────────────────────────────────────

function startSessionTimer() {
    sessionStartTime = Date.now();
    const timerEl = document.getElementById('session-timer');
    if (!timerEl) return;
    timerEl.classList.remove('hidden');
    sessionTimerInterval = setInterval(() => {
        const s = Math.floor((Date.now() - sessionStartTime) / 1000);
        timerEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }, 1000);
}

function stopSessionTimer() {
    if (sessionTimerInterval) { clearInterval(sessionTimerInterval); sessionTimerInterval = null; }
    sessionStartTime = null;
    const timerEl = document.getElementById('session-timer');
    if (timerEl) { timerEl.textContent = '0:00'; timerEl.classList.add('hidden'); }
}

// ── Quick prompt chips ────────────────────────────────────────────────────────

const NURSE_CHIPS = [
    { label: '💊 Drug interaction?',     text: 'Check for drug interactions with the current medications.' },
    { label: '📊 Interpret vitals',       text: 'Interpret the current vitals and flag any concerns.' },
    { label: '🔍 Differential Dx?',       text: 'What are the top differential diagnoses based on the symptoms?' },
    { label: '💉 Next steps?',            text: 'What are the recommended next clinical steps?' },
    { label: '📝 Handover summary',       text: 'Summarise the clinical findings for handover documentation.' },
    { label: '⚠️ Red flags?',             text: 'What red flags should I watch for in this case?' },
    { label: '💧 IV fluids guidance',     text: 'What IV fluid regimen is appropriate here?' },
    { label: '🧪 Labs to order?',         text: 'What additional lab investigations would be helpful?' },
];

const PATIENT_CHIPS = [
    { label: '💊 What is this?',         text: 'What is this medicine and what is it used for?' },
    { label: '⚠️ Side effects?',          text: 'What are the common side effects of this medicine?' },
    { label: '🍽️ Take with food?',        text: 'Should I take this medicine with or without food?' },
    { label: '⏰ Best time to take?',     text: 'What is the best time of day to take this medicine?' },
    { label: '💊 Missed a dose?',         text: 'What should I do if I missed a dose?' },
    { label: '🔄 Interactions?',          text: 'Are there any interactions with other medicines or foods?' },
    { label: '🛑 When to stop?',          text: 'Are there symptoms that mean I should stop taking this medicine?' },
];

function renderQuickChips(mode) {
    const container = document.getElementById('quick-prompts');
    if (!container) return;
    const chips = mode === 'patient' ? PATIENT_CHIPS : NURSE_CHIPS;
    container.innerHTML = chips.map(c =>
        `<button class="quick-chip" data-chip="${c.text.replace(/"/g, '&quot;')}">${c.label}</button>`
    ).join('');
    container.querySelectorAll('.quick-chip').forEach(btn => {
        btn.addEventListener('click', () => {
            if (userInput) { userInput.value = btn.dataset.chip; userInput.focus(); }
            sendMessage();
        });
    });
}

// ── Patient vitals strip ──────────────────────────────────────────────────────

function updateVitalsStrip(patient) {
    const strip = document.getElementById('patient-vitals-strip');
    if (!strip) return;
    if (!patient) { strip.classList.add('hidden'); strip.innerHTML = ''; return; }

    const v = patient.vitals || {};
    const defs = [
        { key: 'bp',         label: 'BP',    classify: x => { const n = parseInt(x); return n > 140 ? 'vital-warn' : n < 90 ? 'vital-alert' : 'vital-ok'; } },
        { key: 'hr',         label: 'HR',    classify: x => { const n = parseInt(x); return n > 100 || n < 50 ? (n > 130 || n < 40 ? 'vital-alert' : 'vital-warn') : 'vital-ok'; } },
        { key: 'spo2',       label: 'SpO₂',  classify: x => { const n = parseInt(x); return n < 90 ? 'vital-alert' : n < 95 ? 'vital-warn' : 'vital-ok'; } },
        { key: 'temp',       label: 'Temp',  classify: x => { const n = parseFloat(x); return n >= 39 ? 'vital-alert' : n >= 37.5 ? 'vital-warn' : 'vital-ok'; } },
        { key: 'rr',         label: 'RR',    classify: x => { const n = parseInt(x); return n > 25 ? 'vital-alert' : n > 20 ? 'vital-warn' : 'vital-ok'; } },
        { key: 'pain_score', label: 'Pain',  classify: x => { const n = parseInt(x); return n >= 7 ? 'vital-alert' : n >= 4 ? 'vital-warn' : 'vital-ok'; } },
    ];

    let items = '';
    for (const { key, label, classify } of defs) {
        const raw = v[key];
        if (!raw) continue;
        const cls = classify(raw);
        // Shorten for display: take first segment before space/slash
        const display = raw.replace(/°C|°F/g, '°').split(' ')[0].split('/').slice(0, 2).join('/').substring(0, 12);
        items += `<div class="vital-item"><div class="vital-label">${label}</div><div class="vital-val ${cls}">${escapeHtml(display)}</div></div>`;
    }

    strip.innerHTML = `
        <span class="text-xs font-bold text-slate-400 uppercase tracking-wide flex-shrink-0">Vitals</span>
        <div class="flex gap-1.5 flex-wrap flex-1">${items}</div>
        <div class="flex items-center gap-1.5 ml-auto flex-shrink-0">
            <span class="text-xs text-slate-400">${escapeHtml(patient.name.split(' ')[0])}</span>
            ${patient.allergies?.length ? `<span class="text-xs bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400 px-2 py-0.5 rounded-full font-bold border border-red-200 dark:border-red-700/40">⚠ ${patient.allergies[0]}</span>` : ''}
            <button onclick="window.open('/patient-files/${patient.id}', '_blank')"
                class="text-xs bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 px-2 py-0.5 rounded-full font-bold border border-amber-200 dark:border-amber-700/40 hover:bg-amber-200 dark:hover:bg-amber-900/60 transition-all cursor-pointer"
                title="Open X-rays, lab reports &amp; imaging">📂 Files</button>
        </div>`;
    strip.classList.remove('hidden');
}

// ── Smart Clinical Features ───────────────────────────────────────────────────

function parseVitalNum(raw) {
    const m = String(raw || '').match(/[\d.]+/);
    return m ? parseFloat(m[0]) : null;
}

function calculateNEWS2(patient) {
    const v = patient.vitals || {};
    let score = 0;
    const breakdown = {};

    const rr = parseVitalNum(v.rr);
    if (rr !== null) {
        const s = rr <= 8 ? 3 : rr <= 11 ? 1 : rr <= 20 ? 0 : rr <= 24 ? 2 : 3;
        score += s; breakdown.rr = s;
    }
    const spo2 = parseVitalNum(v.spo2);
    if (spo2 !== null) {
        const s = spo2 <= 91 ? 3 : spo2 <= 93 ? 2 : spo2 <= 95 ? 1 : 0;
        score += s; breakdown.spo2 = s;
    }
    const bpM = String(v.bp || '').match(/(\d+)/);
    if (bpM) {
        const sbp = parseInt(bpM[1]);
        const s = sbp <= 90 ? 3 : sbp <= 100 ? 2 : sbp <= 110 ? 1 : sbp <= 219 ? 0 : 3;
        score += s; breakdown.sbp = s;
    }
    const hr = parseVitalNum(v.hr);
    if (hr !== null) {
        const s = hr <= 40 ? 3 : hr <= 50 ? 1 : hr <= 90 ? 0 : hr <= 110 ? 1 : hr <= 130 ? 2 : 3;
        score += s; breakdown.hr = s;
    }
    const temp = parseVitalNum(v.temp);
    if (temp !== null) {
        const s = temp <= 35.0 ? 3 : temp <= 36.0 ? 1 : temp <= 38.0 ? 0 : temp <= 39.0 ? 1 : 2;
        score += s; breakdown.temp = s;
    }
    const gcs = parseVitalNum(v.gcs);
    if (gcs !== null && gcs < 15) { score += 3; breakdown.consciousness = 3; }

    const maxSingle = Object.values(breakdown).length ? Math.max(...Object.values(breakdown)) : 0;
    let risk, colorClass;
    if (score >= 7) { risk = 'HIGH'; colorClass = 'score-badge-red'; }
    else if (score >= 5 || maxSingle >= 3) { risk = 'MEDIUM'; colorClass = 'score-badge-amber'; }
    else if (score === 0) { risk = 'MINIMAL'; colorClass = 'score-badge-green'; }
    else { risk = 'LOW'; colorClass = 'score-badge-green'; }
    return { score, risk, colorClass };
}

function calculateQSOFA(patient) {
    const v = patient.vitals || {};
    let score = 0;
    const criteria = [];
    const rr = parseVitalNum(v.rr);
    if (rr !== null && rr >= 22) { score++; criteria.push('RR≥22'); }
    const gcs = parseVitalNum(v.gcs);
    if (gcs !== null && gcs < 15) { score++; criteria.push('Mental↓'); }
    const bpM = String(v.bp || '').match(/(\d+)/);
    if (bpM && parseInt(bpM[1]) <= 100) { score++; criteria.push('SBP≤100'); }

    let risk, colorClass;
    if (score >= 2) { risk = 'POSITIVE'; colorClass = 'score-badge-red'; }
    else if (score === 1) { risk = 'BORDERLINE'; colorClass = 'score-badge-amber'; }
    else { risk = 'NEGATIVE'; colorClass = 'score-badge-green'; }
    return { score, risk, colorClass, criteria };
}

function renderRiskScores(patient) {
    const panel = document.getElementById('risk-scores-panel');
    if (!panel) return;
    if (!patient) { panel.classList.add('hidden'); return; }

    const news2 = calculateNEWS2(patient);
    const qsofa = calculateQSOFA(patient);

    const n2 = document.getElementById('news2-badge');
    if (n2) { n2.textContent = `${news2.score} · ${news2.risk}`; n2.className = `score-badge ${news2.colorClass}`; }

    const qs = document.getElementById('qsofa-badge');
    if (qs) { qs.textContent = `${qsofa.score}/3 · ${qsofa.risk}`; qs.className = `score-badge ${qsofa.colorClass}`; }

    panel.classList.remove('hidden');

    if (news2.score >= 7) {
        showUrgentAlert(
            `NEWS2 Score: ${news2.score} — HIGH clinical deterioration risk`,
            'Escalate to senior clinician immediately. Consider critical care review.'
        );
        showToast(`NEWS2 ${news2.score} — HIGH deterioration risk`, 'error', 6000);
    } else if (qsofa.score >= 2) {
        showUrgentAlert(
            `qSOFA Score: ${qsofa.score}/3 — Sepsis possible (${qsofa.criteria.join(', ')})`,
            'Initiate sepsis bundle: blood cultures, lactate, IV access. Urgent medical review.'
        );
        showToast(`qSOFA ${qsofa.score}/3 — Sepsis screening required`, 'error', 6000);
    }
}

async function generateVisualAid() {
    const prompt = userInput?.value?.trim();
    if (!prompt) { showToast('Type a description for the image you want to generate', 'warning'); return; }

    addMessage('user', `🎨 Generate visual: ${prompt}`);
    userInput.value = '';
    aiThinking?.classList.remove('hidden');
    const thinkingText = document.getElementById('ai-thinking-text');
    if (thinkingText) thinkingText.textContent = 'Generating visual aid...';

    try {
        const res = await fetch('/api/generate-image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt })
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        if (data.image) {
            addImageMessage(data.image, data.mime_type || 'image/png', data.caption || prompt);
            showToast('Visual aid generated', 'success', 3000);
        } else {
            throw new Error('No image returned');
        }
    } catch (err) {
        aiThinking?.classList.add('hidden');
        addMessage('assistant', `⚠️ Could not generate image: ${err.message}`);
        showToast('Image generation failed', 'error');
    }
}

async function generateSBAR() {
    if (!currentPatientData) { showToast('Load a patient first', 'warning'); return; }
    const modal = document.getElementById('sbar-modal');
    const content = document.getElementById('sbar-content');
    if (!modal || !content) return;

    modal.classList.remove('hidden');
    content.innerHTML = `<div class="flex items-center justify-center gap-3 py-8 text-slate-400">
        <div class="loading-dots"><span></span><span></span><span></span></div>
        <span>Generating SBAR note...</span></div>`;
    content.dataset.rawText = '';

    try {
        const notesRes = await fetch(`/api/clinical-notes?session_id=${sessionId}`);
        const notesData = await notesRes.json();
        const res = await fetch('/api/generate-sbar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ patient_id: currentPatientData.id, clinical_notes: notesData.notes || [] })
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        content.dataset.rawText = data.sbar;
        content.innerHTML = formatSbarText(data.sbar);
        showToast('SBAR handover note generated', 'success', 3000);
    } catch (err) {
        content.innerHTML = `<div class="text-red-500 text-sm p-4">Failed to generate SBAR: ${escapeHtml(err.message)}</div>`;
        showToast('SBAR generation failed', 'error');
    }
}

function formatSbarText(raw) {
    const sectionColors = { SITUATION: '#0891b2', BACKGROUND: '#7c3aed', ASSESSMENT: '#b45309', RECOMMENDATION: '#059669' };
    let html = escapeHtml(raw);
    ['SITUATION', 'BACKGROUND', 'ASSESSMENT', 'RECOMMENDATION'].forEach(s => {
        const color = sectionColors[s];
        html = html.replace(
            new RegExp(`\\*\\*${s}\\*\\*`, 'g'),
            `<div style="margin-top:16px;margin-bottom:6px;padding-bottom:4px;border-bottom:2px solid ${color};display:flex;align-items:center;gap:6px">` +
            `<span style="color:${color};font-weight:800;font-size:11px;letter-spacing:0.08em">${s}</span></div>`
        );
    });
    html = html.replace(/\n\n/g, '</p><p style="margin:4px 0 10px">').replace(/\n/g, '<br>');
    return `<p style="margin:0 0 8px">${html}</p>`;
}

async function generateDDx() {
    if (!currentPatientData) { showToast('Load a patient first', 'warning'); return; }
    const panel = document.getElementById('ddx-panel');
    const content = document.getElementById('ddx-content');
    if (!panel || !content) return;

    panel.classList.remove('hidden');
    content.innerHTML = `<div class="flex items-center gap-2 py-3 text-slate-400 justify-center">
        <div class="loading-dots"><span></span><span></span><span></span></div>
        <span>Generating differentials...</span></div>`;

    try {
        const res = await fetch('/api/generate-ddx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ patient_id: currentPatientData.id, session_context: '' })
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        let html = '';
        if (data.red_flags?.length) {
            html += `<div class="px-2.5 py-2 rounded-xl bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/40">
                <p class="font-bold text-red-700 dark:text-red-300 mb-1">🚨 Red Flags</p>
                ${data.red_flags.map(f => `<p class="text-red-600 dark:text-red-400">• ${escapeHtml(f)}</p>`).join('')}
            </div>`;
        }
        if (data.immediate_priority) {
            html += `<div class="px-2.5 py-1.5 rounded-xl bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700/40">
                <p class="font-bold text-amber-700 dark:text-amber-300">⚡ ${escapeHtml(data.immediate_priority)}</p>
            </div>`;
        }
        (data.differentials || []).forEach(d => {
            const lk = (d.likelihood || '').toLowerCase();
            const cls = lk === 'high' ? 'ddx-item-high' : lk === 'medium' ? 'ddx-item-medium' : 'ddx-item-low';
            const lkColor = lk === 'high' ? 'text-red-600 dark:text-red-400' : lk === 'medium' ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400';
            html += `<div class="ddx-item ${cls}">
                <div class="flex items-start justify-between gap-1 mb-1">
                    <span class="font-bold text-slate-700 dark:text-slate-200">${d.rank}. ${escapeHtml(d.diagnosis)}</span>
                    <span class="font-bold ${lkColor} flex-shrink-0">${escapeHtml(d.likelihood)}</span>
                </div>
                <p class="text-slate-600 dark:text-slate-400 mb-1">📊 ${escapeHtml(d.key_evidence)}</p>
                <p class="font-semibold text-slate-700 dark:text-slate-300">→ ${escapeHtml(d.next_step)}</p>
            </div>`;
        });
        content.innerHTML = html || '<p class="text-slate-400 italic text-center py-4">No differentials returned.</p>';
        showToast('Differential diagnosis generated', 'success', 3000);
    } catch (err) {
        content.innerHTML = `<div class="text-red-500 p-3">Failed: ${escapeHtml(err.message)}</div>`;
        showToast('DDx generation failed', 'error');
    }
}

// ── Procedure Checklist Mode ──────────────────────────────────────────────────

let activeProcedureData = null;

async function loadProcedureLibrary() {
    const selector = document.getElementById('procedure-selector');
    if (!selector) return;
    try {
        const res = await fetch('/api/procedures');
        const data = await res.json();
        const procs = data.procedures || [];
        if (!procs.length) return;

        selector.innerHTML = '<p class="text-xs text-slate-400 dark:text-slate-500 italic text-center py-1 mb-1">Select a procedure to start guided mode</p>';
        procs.forEach(proc => {
            const btn = document.createElement('button');
            btn.className = 'procedure-select-btn';
            btn.innerHTML = `
                <span class="text-base">${proc.icon}</span>
                <div class="flex-1 min-w-0 text-left">
                    <div class="font-bold text-xs text-slate-700 dark:text-slate-200 truncate">${escapeHtml(proc.name)}</div>
                    <div class="text-xs text-slate-400 dark:text-slate-500">${proc.steps.length} steps · ${escapeHtml(proc.category)}</div>
                </div>
                <span class="text-slate-300 dark:text-slate-600">›</span>
            `;
            btn.addEventListener('click', () => startProcedure(proc.id));
            selector.appendChild(btn);
        });
    } catch (err) {
        console.error('Failed to load procedures:', err);
    }
}

async function startProcedure(procedureId) {
    try {
        const res = await fetch('/api/start-procedure', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ procedure_id: procedureId })
        });
        const data = await res.json();
        if (!data.success) { showToast(data.message || 'Failed to start procedure', 'error'); return; }

        activeProcedureData = data.procedure;
        renderProcedureSteps();
        showToast(`📋 ${activeProcedureData.name} — Guided mode active`, 'success', 4000);
        addSystemMessage(`📋 <strong>Procedure started:</strong> ${escapeHtml(activeProcedureData.name)} (${activeProcedureData.steps.length} steps). Turn on your camera and the AI will guide you through each step.`);

        // If connected, notify the AI about the procedure
        if (isConnected && socket) {
            socket.emit('send_text_message', {
                session_id: sessionId,
                text: `[SYSTEM: Procedure checklist "${activeProcedureData.name}" has been activated with ${activeProcedureData.steps.length} steps. Please guide me through each step. I will use my camera so you can verify each step visually. Start with Step 1.]`
            });
            aiThinking?.classList.remove('hidden');
        }
    } catch (err) {
        showToast('Failed to start procedure', 'error');
    }
}

async function stopProcedure() {
    try {
        await fetch('/api/stop-procedure', { method: 'POST' });
    } catch (_) {}

    const completed = activeProcedureData?.steps?.filter(s => s.status === 'verified').length || 0;
    const total = activeProcedureData?.steps?.length || 0;
    const name = activeProcedureData?.name || 'Procedure';

    activeProcedureData = null;
    renderProcedureSteps();
    showToast('Procedure ended', 'info');
    addSystemMessage(`📋 <strong>${escapeHtml(name)}</strong> ended. Completed ${completed}/${total} steps.`);
}

function renderProcedureSteps() {
    const selector = document.getElementById('procedure-selector');
    const stepsContainer = document.getElementById('procedure-steps');
    const title = document.getElementById('procedure-panel-title');
    const progress = document.getElementById('procedure-progress');
    const stopBtn = document.getElementById('stop-procedure-btn');

    if (!activeProcedureData) {
        selector?.classList.remove('hidden');
        stepsContainer?.classList.add('hidden');
        if (title) title.textContent = 'Procedures';
        progress?.classList.add('hidden');
        stopBtn?.classList.add('hidden');
        return;
    }

    selector?.classList.add('hidden');
    stepsContainer?.classList.remove('hidden');
    stopBtn?.classList.remove('hidden');
    if (title) title.textContent = activeProcedureData.name;

    const verified = activeProcedureData.steps.filter(s => s.status === 'verified').length;
    const total = activeProcedureData.steps.length;
    if (progress) {
        progress.textContent = `${verified}/${total}`;
        progress.classList.remove('hidden');
    }

    stepsContainer.innerHTML = '';

    // Progress bar
    const pct = total > 0 ? (verified / total) * 100 : 0;
    const barDiv = document.createElement('div');
    barDiv.className = 'procedure-progress-bar';
    barDiv.innerHTML = `<div class="procedure-progress-fill" style="width:${pct}%"></div>`;
    stepsContainer.appendChild(barDiv);

    activeProcedureData.steps.forEach(s => {
        const div = document.createElement('div');
        div.className = `procedure-step procedure-step-${s.status}`;
        div.id = `procedure-step-${s.step}`;

        const icon = { pending: '⬜', verified: '✅', warning: '⚠️', flagged: '🚫' }[s.status] || '⬜';
        const obs = s.observation ? `<p class="procedure-step-obs">${escapeHtml(s.observation)}</p>` : '';

        div.innerHTML = `
            <div class="flex items-start gap-2">
                <span class="procedure-step-icon">${icon}</span>
                <div class="flex-1 min-w-0">
                    <div class="procedure-step-title">Step ${s.step}: ${escapeHtml(s.title)}</div>
                    <div class="procedure-step-detail">${escapeHtml(s.detail)}</div>
                    <div class="procedure-step-visual">👁 ${escapeHtml(s.visual_check)}</div>
                    ${obs}
                </div>
            </div>
        `;
        stepsContainer.appendChild(div);
    });

    // Check if all steps completed
    if (verified === total && total > 0) {
        const done = document.createElement('div');
        done.className = 'procedure-complete';
        done.innerHTML = '🎉 All steps verified!';
        stepsContainer.appendChild(done);
    }
}

function updateProcedureStepUI(stepNumber, status, observation) {
    if (!activeProcedureData) return;

    const step = activeProcedureData.steps.find(s => s.step === stepNumber);
    if (step) {
        step.status = status;
        step.observation = observation;
    }
    renderProcedureSteps();

    // Scroll the step into view
    const el = document.getElementById(`procedure-step-${stepNumber}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // Toast + sound cue
    if (status === 'verified') {
        showToast(`✅ Step ${stepNumber} verified`, 'success', 2000);
    } else if (status === 'flagged') {
        showToast(`🚫 Step ${stepNumber} flagged — check AI guidance`, 'error', 4000);
    } else if (status === 'warning') {
        showToast(`⚠️ Step ${stepNumber} — minor concern noted`, 'warning', 3000);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
