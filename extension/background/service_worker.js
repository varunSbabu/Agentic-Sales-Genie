// =============================================================================
// Sales Genie — MV3 service worker
//
// Responsibilities:
//   - Central message router for the sidebar, content scripts, and offscreen doc
//   - Auth token storage (chrome.storage.local)
//   - Recording session lifecycle (start/stop) via an offscreen document
//     (MediaRecorder cannot run in a service worker — it has no DOM)
//   - Backend config (base URL) resolution
//
// The offscreen document (background/offscreen.html + audio_capture.js) does the
// actual tabCapture + MediaRecorder work and streams chunks to the backend.
// =============================================================================

const DEFAULT_BACKEND = "http://localhost:8000";
const OFFSCREEN_PATH = "background/offscreen.html";

// In-memory recording state (mirrored to storage for sidebar reads)
let recordingState = {
  active: false,
  sessionId: null,
  tabId: null,
  platform: null,
  startedAt: null,
  lastJobId: null,
};

// ---------------------------------------------------------------------------
// Config + auth helpers
// ---------------------------------------------------------------------------
async function getBackendUrl() {
  const { backendUrl } = await chrome.storage.local.get("backendUrl");
  return backendUrl || DEFAULT_BACKEND;
}

async function getAuthToken() {
  const { authToken } = await chrome.storage.local.get("authToken");
  return authToken || null;
}

async function saveAuthToken(token, refresh) {
  await chrome.storage.local.set({ authToken: token, refreshToken: refresh || null });
}

async function clearAuth() {
  await chrome.storage.local.remove(["authToken", "refreshToken"]);
}

async function authedFetch(path, options = {}) {
  const base = await getBackendUrl();
  const token = await getAuthToken();
  const headers = Object.assign({}, options.headers || {});
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (!headers["Content-Type"] && options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${base}${path}`, { ...options, headers });
  return res;
}

// ---------------------------------------------------------------------------
// Offscreen document management (required for MediaRecorder in MV3)
// ---------------------------------------------------------------------------
async function hasOffscreen() {
  // getContexts is available in newer Chrome; fall back gracefully
  if (chrome.runtime.getContexts) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
    });
    return contexts.length > 0;
  }
  return false;
}

async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_PATH,
    reasons: ["USER_MEDIA"],
    justification: "Record tab audio for sales-call transcription.",
  });
}

async function closeOffscreen() {
  if (await hasOffscreen()) {
    await chrome.offscreen.closeDocument();
  }
}

// ---------------------------------------------------------------------------
// Recording lifecycle
// ---------------------------------------------------------------------------
async function startRecording(tabId, platform) {
  const token = await getAuthToken();
  if (!token) throw new Error("Not logged in — open the sidebar and log in first.");

  // 1. Ask the backend to open a recording session
  const startRes = await authedFetch("/recording/start", {
    method: "POST",
    body: JSON.stringify({ platform: platform || "extension" }),
  });
  if (!startRes.ok) {
    const err = await startRes.text();
    throw new Error(`Backend rejected recording start: ${err}`);
  }
  const { session_id } = await startRes.json();

  // 2. Get a MediaStream ID for this tab (must be called from SW with a user gesture)
  const streamId = await new Promise((resolve, reject) => {
    chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, (id) => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      resolve(id);
    });
  });

  // 3. Spin up the offscreen doc and tell it to start recording
  await ensureOffscreen();
  const base = await getBackendUrl();
  await chrome.runtime.sendMessage({
    target: "offscreen",
    type: "OFFSCREEN_START",
    streamId,
    sessionId: session_id,
    backendUrl: base,
    authToken: token,
  });

  recordingState = {
    active: true,
    sessionId: session_id,
    tabId,
    platform,
    startedAt: Date.now(),
    lastJobId: null,
  };
  await chrome.storage.local.set({ recordingState });
  return recordingState;
}

async function stopRecording(durationSecs) {
  if (!recordingState.active) return { ok: false, error: "no active recording" };

  // 1. Tell offscreen to stop + flush the final chunk
  try {
    await chrome.runtime.sendMessage({ target: "offscreen", type: "OFFSCREEN_STOP" });
  } catch (e) {
    // offscreen may already be gone — continue to backend stop
  }
  await closeOffscreen();

  // 2. Tell the backend to assemble + transcribe + queue analysis
  const stopRes = await authedFetch("/recording/stop", {
    method: "POST",
    body: JSON.stringify({
      session_id: recordingState.sessionId,
      platform: recordingState.platform || "extension",
      duration_secs: durationSecs || Math.round((Date.now() - (recordingState.startedAt || Date.now())) / 1000),
    }),
  });

  const wasActive = { ...recordingState };
  recordingState = { active: false, sessionId: null, tabId: null, platform: null, startedAt: null, lastJobId: null };

  if (!stopRes.ok) {
    const err = await stopRes.text();
    await chrome.storage.local.set({ recordingState });
    return { ok: false, error: `Backend stop failed: ${err}` };
  }
  const data = await stopRes.json();
  if (data.error) {
    await chrome.storage.local.set({ recordingState });
    return { ok: false, error: data.error };
  }

  recordingState.lastJobId = data.job_id;
  await chrome.storage.local.set({ recordingState, lastStopResult: data });
  return { ok: true, ...data };
}

// ---------------------------------------------------------------------------
// Message router
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Offscreen-targeted messages are handled inside the offscreen doc, ignore here
  if (msg.target === "offscreen") return false;

  (async () => {
    try {
      switch (msg.type) {
        case "START_RECORDING": {
          const tabId = msg.tabId || (sender.tab && sender.tab.id);
          const state = await startRecording(tabId, msg.platform);
          sendResponse({ ok: true, state });
          break;
        }
        case "STOP_RECORDING": {
          const result = await stopRecording(msg.durationSecs);
          sendResponse(result);
          break;
        }
        case "GET_RECORDING_STATE": {
          const { recordingState: stored } = await chrome.storage.local.get("recordingState");
          sendResponse({ ok: true, state: stored || recordingState });
          break;
        }
        case "SAVE_AUTH_TOKEN": {
          await saveAuthToken(msg.token, msg.refresh);
          sendResponse({ ok: true });
          break;
        }
        case "GET_AUTH_TOKEN": {
          const token = await getAuthToken();
          sendResponse({ ok: true, token });
          break;
        }
        case "CLEAR_AUTH": {
          await clearAuth();
          sendResponse({ ok: true });
          break;
        }
        case "GET_BACKEND_URL": {
          sendResponse({ ok: true, backendUrl: await getBackendUrl() });
          break;
        }
        case "CALL_DETECTED": {
          // Content script telling us a call UI is present
          await chrome.storage.local.set({
            detectedCall: { platform: msg.platform, tabId: sender.tab && sender.tab.id, at: Date.now() },
          });
          // Badge to nudge the user
          if (sender.tab) {
            chrome.action.setBadgeText({ text: "●", tabId: sender.tab.id });
            chrome.action.setBadgeBackgroundColor({ color: "#2f81f7", tabId: sender.tab.id });
          }
          sendResponse({ ok: true });
          break;
        }
        case "CALL_ENDED": {
          await chrome.storage.local.remove("detectedCall");
          if (sender.tab) chrome.action.setBadgeText({ text: "", tabId: sender.tab.id });
          sendResponse({ ok: true });
          break;
        }
        case "OFFSCREEN_ERROR": {
          // Recording failed inside the offscreen doc
          recordingState.active = false;
          await chrome.storage.local.set({ recordingState, recordingError: msg.error });
          sendResponse({ ok: true });
          break;
        }
        default:
          sendResponse({ ok: false, error: `unknown message type: ${msg.type}` });
      }
    } catch (err) {
      sendResponse({ ok: false, error: err.message || String(err) });
    }
  })();

  return true; // keep the message channel open for the async response
});

// Clear the badge when a tab navigates away
chrome.tabs.onRemoved.addListener((tabId) => {
  if (recordingState.tabId === tabId && recordingState.active) {
    stopRecording();
  }
});
