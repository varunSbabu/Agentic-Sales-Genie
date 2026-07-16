// =============================================================================
// Sales Genie — offscreen audio capture
//
// Runs inside background/offscreen.html (an offscreen document) because MV3
// service workers cannot use MediaRecorder. Receives a tabCapture streamId from
// the service worker, records BOTH the tab audio (the remote participants) and
// the local microphone (the rep), mixes them, and POSTs 1-second base64 chunks
// to the backend's /recording/chunk endpoint.
//
// Capturing the mic is essential: tab audio only contains what plays through
// the tab (the remote side). Without the mic, the local user's half of the call
// is never recorded, so the transcript is one-sided and scoring is meaningless.
// =============================================================================

let mediaRecorder = null;
let tabStream = null;      // remote participants (tab audio)
let micStream = null;      // local microphone (the rep)
let audioContext = null;   // mixes tab + mic for RECORDING only
let audioNodes = [];        // hold node refs so the graph isn't garbage-collected
let ctx = null; // { sessionId, backendUrl, authToken, seq }
let autoStopTimer = null;

// Hard cap so a forgotten recording can never run indefinitely.
const MAX_RECORDING_MS = 60 * 60 * 1000; // 60 minutes

function reportError(message) {
  chrome.runtime.sendMessage({ type: "OFFSCREEN_ERROR", error: message });
}

async function uploadChunk(blob) {
  if (!ctx) return;
  try {
    const buf = await blob.arrayBuffer();
    // Convert to base64 without blowing the call stack on large chunks
    const bytes = new Uint8Array(buf);
    let binary = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    const b64 = btoa(binary);

    await fetch(`${ctx.backendUrl}/recording/chunk`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${ctx.authToken}`,
      },
      body: JSON.stringify({
        session_id: ctx.sessionId,
        chunk_base64: b64,
        seq: ctx.seq++,
      }),
    });
  } catch (err) {
    // A dropped chunk is NOT fatal: the recorder keeps producing chunks and the
    // next upload may well succeed. Reporting this as an OFFSCREEN_ERROR would
    // mark the whole session inactive while capture is still running — which
    // then makes Stop fail with "no active recording". Just log and continue.
    console.warn(`Sales Genie: chunk ${ctx ? ctx.seq - 1 : "?"} upload failed:`, err);
  }
}

async function startCapture({ streamId, sessionId, backendUrl, authToken }) {
  ctx = { sessionId, backendUrl, authToken, seq: 0 };
  try {
    // 1. Tab audio (remote participants) from the id the SW handed us.
    tabStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId,
        },
      },
      video: false,
    });

    // 2. Microphone (the local user / rep). If the extension hasn't been granted
    // mic access yet this throws — we then record tab-only rather than fail, and
    // flag it so the UI can prompt the user to enable the mic in Settings.
    micStream = null;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      await chrome.storage.local.set({ micUnavailable: false });
    } catch (micErr) {
      await chrome.storage.local.set({ micUnavailable: true });
      console.warn("Sales Genie: microphone unavailable — recording remote audio only:", micErr);
    }

    // 3. One AudioContext, one source node per stream, two sinks:
    //    - tab → audioContext.destination : plays the remote side to the speakers
    //      (the beep test proved this output path works now that the offscreen
    //      doc has the AUDIO_PLAYBACK reason). This keeps the tab audible.
    //    - tab + mic → mixDest : the mixed stream we record. Mic is NOT connected
    //      to destination, so there's no echo/feedback.
    // Everything stays inside Web Audio (no <audio> element / cloned track, which
    // produced a silent clone). Node refs are held in audioNodes so the graph
    // isn't garbage-collected mid-call.
    audioContext = new AudioContext();
    const mixDest = audioContext.createMediaStreamDestination();
    const tabSource = audioContext.createMediaStreamSource(tabStream);
    tabSource.connect(mixDest);                  // record
    tabSource.connect(audioContext.destination); // hear the remote side
    audioNodes = [mixDest, tabSource];
    if (micStream) {
      const micSource = audioContext.createMediaStreamSource(micStream);
      micSource.connect(mixDest);                // record only
      audioNodes.push(micSource);
    }
    if (audioContext.state !== "running") {
      try { await audioContext.resume(); } catch (e) { console.warn("[SG] resume() failed:", e); }
    }
    const diag = `ctx=${audioContext.state} tabTracks=${tabStream.getAudioTracks().length} mic=${micStream ? 1 : 0}`;
    console.log("[SG] recording ready —", diag);
    // Surface it to the popup (offscreen console is hard to inspect live).
    await chrome.storage.local.set({ sgAudioDiag: diag });
    // Debug handle so the captured stream can be tested from the offscreen console.
    try { self.__sgDebug = { tabStream, audioContext }; } catch (e) {}

    mediaRecorder = new MediaRecorder(mixDest.stream, {
      mimeType: "audio/webm;codecs=opus",
    });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) uploadChunk(e.data);
    };
    mediaRecorder.onerror = (e) => reportError(`MediaRecorder error: ${e.error?.name || "unknown"}`);

    // 1-second timeslice → one chunk per second
    mediaRecorder.start(1000);

    // Safety net: auto-stop after the max duration and tell the service worker
    // to finalize, so a recording can never run forever if the user forgets.
    autoStopTimer = setTimeout(() => {
      chrome.runtime.sendMessage({ type: "OFFSCREEN_AUTOSTOP" });
      stopCapture();
    }, MAX_RECORDING_MS);
  } catch (err) {
    reportError(`could not start capture: ${err.message || err}`);
  }
}

function stopCapture() {
  try {
    if (autoStopTimer) { clearTimeout(autoStopTimer); autoStopTimer = null; }
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
    if (tabStream) tabStream.getTracks().forEach((t) => t.stop());
    if (micStream) micStream.getTracks().forEach((t) => t.stop());
    if (audioContext && audioContext.state !== "closed") audioContext.close();
  } catch (err) {
    reportError(`stop failed: ${err.message || err}`);
  } finally {
    mediaRecorder = null;
    tabStream = null;
    micStream = null;
    audioContext = null;
    audioNodes = [];
    ctx = null;
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.target !== "offscreen") return false;
  if (msg.type === "OFFSCREEN_START") {
    startCapture(msg).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === "OFFSCREEN_STOP") {
    stopCapture();
    sendResponse({ ok: true });
    return true;
  }
  return false;
});
