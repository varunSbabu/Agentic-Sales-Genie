// =============================================================================
// Sales Genie — offscreen audio capture
//
// Runs inside background/offscreen.html (an offscreen document) because MV3
// service workers cannot use MediaRecorder. Receives a tabCapture streamId from
// the service worker, records tab audio, and POSTs 1-second base64 chunks to
// the backend's /recording/chunk endpoint.
// =============================================================================

let mediaRecorder = null;
let mediaStream = null;
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
    reportError(`chunk upload failed: ${err.message || err}`);
  }
}

async function startCapture({ streamId, sessionId, backendUrl, authToken }) {
  ctx = { sessionId, backendUrl, authToken, seq: 0 };
  try {
    // Build the tab-capture MediaStream from the id the SW handed us
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId,
        },
      },
      video: false,
    });

    // Keep the tab audio audible to the user while we record it. Without this,
    // routing audio into the capture stream mutes the tab for the listener.
    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(mediaStream);
    source.connect(audioContext.destination);

    mediaRecorder = new MediaRecorder(mediaStream, {
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
    if (mediaStream) {
      mediaStream.getTracks().forEach((t) => t.stop());
    }
  } catch (err) {
    reportError(`stop failed: ${err.message || err}`);
  } finally {
    mediaRecorder = null;
    mediaStream = null;
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
