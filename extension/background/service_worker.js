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
const WATCHDOG_ALARM = "sg-recording-watchdog";
// If the meeting page stops sending heartbeats for this long, assume the user
// left / closed the meeting and auto-stop the recording.
const HEARTBEAT_TIMEOUT_MS = 45 * 1000;

// In-memory recording state (mirrored to storage for sidebar reads)
let recordingState = {
  active: false,
  sessionId: null,
  tabId: null,
  platform: null,
  startedAt: null,
  lastJobId: null,
};

// MV3 service workers are ephemeral: Chrome tears them down after ~30s idle and
// restarts them fresh on the next event, wiping in-memory state. The offscreen
// doc keeps recording (it uploads chunks itself), but the worker forgets the
// session — so anything that reads recordingState must first rehydrate it from
// storage, or a mid-call Stop lands on active:false and no-ops.
async function loadRecordingState() {
  const { recordingState: stored } = await chrome.storage.local.get("recordingState");
  if (stored) recordingState = stored;
  return recordingState;
}

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
  // Always recreate a FRESH offscreen document. A lingering one from a previous
  // session runs stale code (and may have been created with old reasons), which
  // silently defeats any recording/playback fix. Closing + recreating guarantees
  // the current audio_capture.js and the current reasons are in effect.
  if (await hasOffscreen()) {
    try { await chrome.offscreen.closeDocument(); } catch (e) { /* already gone */ }
  }
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_PATH,
    // USER_MEDIA: capture tab + mic. AUDIO_PLAYBACK: play the captured tab audio
    // back to the user — without this reason the offscreen doc can capture but
    // its audio output is silent, so the remote side isn't audible while recording.
    reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
    justification: "Record and play back tab + mic audio for sales-call transcription.",
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
  // Seed the heartbeat so the watchdog doesn't fire before the first ping, and
  // arm the watchdog alarm (see onAlarm below).
  await chrome.storage.local.set({ recordingState, lastHeartbeatAt: Date.now() });
  chrome.alarms.create(WATCHDOG_ALARM, { periodInMinutes: 0.5 });
  return recordingState;
}

async function stopRecording(durationSecs) {
  await loadRecordingState(); // worker may have been recycled mid-call
  // Honor Stop as long as there's a session to close. We deliberately don't
  // gate on `active` alone: a non-fatal hiccup could have flipped that flag
  // while capture kept running, and refusing to stop would strand the session.
  if (!recordingState.active && !recordingState.sessionId) {
    return { ok: false, error: "no active recording" };
  }

  // Snapshot the session before we tear it down; the backend call below needs it.
  const session = { ...recordingState };

  // 1. Kill the local recorder FIRST and clear our state UNCONDITIONALLY. This
  // must never depend on the backend: if the token has expired (or the server
  // is down), the capture still has to stop and the session must not linger, or
  // the popup would resume a phantom timer that can never be ended.
  try {
    await chrome.runtime.sendMessage({ target: "offscreen", type: "OFFSCREEN_STOP" });
  } catch (e) {
    // offscreen may already be gone — that's fine, capture is stopped either way
  }
  await closeOffscreen();
  chrome.alarms.clear(WATCHDOG_ALARM);
  recordingState = { active: false, sessionId: null, tabId: null, platform: null, startedAt: null, lastJobId: null };
  await chrome.storage.local.set({ recordingState });

  // 2. Best-effort: ask the backend to assemble + transcribe + queue analysis.
  // A failure here means we lose the analysis for this call, NOT that recording
  // is still running — so we report it distinctly instead of as "stop failed".
  let stopRes;
  try {
    stopRes = await authedFetch("/recording/stop", {
      method: "POST",
      body: JSON.stringify({
        session_id: session.sessionId,
        platform: session.platform || "extension",
        duration_secs: durationSecs || Math.round((Date.now() - (session.startedAt || Date.now())) / 1000),
      }),
    });
  } catch (e) {
    return { ok: false, stopped: true, error: `Recording stopped, but couldn't reach the backend: ${e.message || e}` };
  }

  if (!stopRes.ok) {
    const err = await stopRes.text();
    // Token expired mid-call is common on long recordings; flag it so the popup
    // can prompt a re-login rather than showing a scary generic error.
    const needsReauth = stopRes.status === 401;
    return { ok: false, stopped: true, needsReauth, error: needsReauth
      ? "Recording stopped, but your session expired — please log in again to analyze it."
      : `Recording stopped, but the backend couldn't finalize it: ${err}` };
  }
  const data = await stopRes.json();
  if (data.error) {
    return { ok: false, stopped: true, error: data.error };
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
          const state = await loadRecordingState();
          // Zombie check: if storage says we're recording but no offscreen
          // document exists, the recorder is actually gone (e.g. the extension
          // was reloaded, which tears down offscreen but leaves storage intact).
          // Clear it so the popup doesn't resume a phantom timer that can never
          // be stopped for real.
          if (state.active && !(await hasOffscreen())) {
            recordingState = { active: false, sessionId: null, tabId: null, platform: null, startedAt: null, lastJobId: null };
            await chrome.storage.local.set({ recordingState });
            sendResponse({ ok: true, state: recordingState });
            break;
          }
          sendResponse({ ok: true, state });
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
        case "CALL_HEARTBEAT": {
          // Meeting page telling us the call is still live. Refresh the timestamp
          // the watchdog checks against.
          await chrome.storage.local.set({ lastHeartbeatAt: Date.now() });
          sendResponse({ ok: true });
          break;
        }
        case "CALL_ENDED": {
          await chrome.storage.local.remove("detectedCall");
          if (sender.tab) chrome.action.setBadgeText({ text: "", tabId: sender.tab.id });
          // If we're recording this tab's call, stop when the call ends so the
          // recording doesn't keep running after the user leaves the meeting.
          await loadRecordingState();
          if (recordingState.active && sender.tab && recordingState.tabId === sender.tab.id) {
            await stopRecording();
          }
          sendResponse({ ok: true });
          break;
        }
        case "OFFSCREEN_AUTOSTOP": {
          // Recorder hit the max-duration cap — finalize like a normal stop.
          const result = await stopRecording();
          await chrome.storage.local.set({ recordingAutoStopped: true });
          sendResponse({ ok: true, autostopped: true, ...result });
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
chrome.tabs.onRemoved.addListener(async (tabId) => {
  await loadRecordingState(); // worker may have been recycled since start
  if (recordingState.tabId === tabId && recordingState.active) {
    stopRecording();
  }
});

// Does this URL still look like an active call? Leaving a meeting navigates the
// tab to a lobby/landing URL — a DOM-independent, reliable "call ended" signal.
// Unknown hosts return true (don't stop) so we never cut a call short.
function stillInCallUrl(url) {
  if (!url) return true;
  try {
    const { hostname: h, pathname: p } = new URL(url);
    if (h.includes("meet.google.com")) return /^\/[a-z]{3}-[a-z]{3,4}-[a-z]{3}(\/|$)/i.test(p);
    if (h.includes("zoom.us")) return /\/(wc|j)\//.test(p) || p.includes("/meeting");
    if (h.includes("gong.io")) return p.includes("/call");
    return true; // teams + anything else: rely on CALL_ENDED / watchdog
  } catch (e) { return true; }
}

// Primary auto-stop: when the recorded tab navigates OUT of the call (e.g. Meet
// → /landing), stop immediately. Reliable and instant, unlike DOM detection.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (!changeInfo.url) return;
  await loadRecordingState();
  if (recordingState.active && recordingState.tabId === tabId && !stillInCallUrl(changeInfo.url)) {
    stopRecording();
  }
});

// Watchdog: fires periodically while a recording is armed. If the meeting page
// has stopped sending heartbeats (user left, closed the tab, or the page was
// refreshed and never re-entered the call), auto-stop so a recording can never
// outlive the meeting. This is the safety net behind CALL_ENDED / onRemoved.
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== WATCHDOG_ALARM) return;
  await loadRecordingState();
  if (!recordingState.active) {
    chrome.alarms.clear(WATCHDOG_ALARM);
    return;
  }
  const { lastHeartbeatAt } = await chrome.storage.local.get("lastHeartbeatAt");
  const since = Date.now() - (lastHeartbeatAt || recordingState.startedAt || Date.now());
  if (since > HEARTBEAT_TIMEOUT_MS) {
    await stopRecording();
    await chrome.storage.local.set({ recordingAutoStopped: true });
  }
});

// ---------------------------------------------------------------------------
// Dev hot-reload — reloads the extension automatically when its source files
// change on disk, so you don't have to click reload in chrome://extensions.
// Chrome has no built-in file watcher for extensions; this polls the packaged
// files (which, for an UNPACKED extension, are read fresh from disk) and calls
// chrome.runtime.reload() on any change. Guarded to development: a packed /
// Web-Store build has an `update_url` in its manifest, so this never runs there.
// ---------------------------------------------------------------------------
const DEV_MODE = !("update_url" in chrome.runtime.getManifest());
const HOT_RELOAD_FILES = [
  "manifest.json",
  "background/service_worker.js", "background/audio_capture.js", "background/offscreen.html",
  "content/content_script.js",
  "content/platform_extractors/meet.js", "content/platform_extractors/zoom.js",
  "content/platform_extractors/gong.js", "content/platform_extractors/generic.js",
  "sidebar/sidebar.html", "sidebar/sidebar.js", "sidebar/sidebar.css",
  "config/settings.html", "config/settings.js",
];

async function hotReloadFingerprint() {
  const parts = await Promise.all(HOT_RELOAD_FILES.map(async (f) => {
    try { return await (await fetch(chrome.runtime.getURL(f), { cache: "no-store" })).text(); }
    catch (e) { return ""; }
  }));
  const joined = parts.join(" ");
  let h = 5381; // djb2 — cheap but sensitive to any edit
  for (let i = 0; i < joined.length; i++) h = ((h << 5) + h + joined.charCodeAt(i)) | 0;
  return `${joined.length}:${h}`;
}

async function initHotReload() {
  if (!DEV_MODE) return;
  let last = await hotReloadFingerprint(); // baseline = current on-disk state
  setInterval(async () => {
    // Off switch (Settings → toggle). Checked every tick so it takes effect live.
    const { devAutoReloadDisabled } = await chrome.storage.local.get("devAutoReloadDisabled");
    if (devAutoReloadDisabled) return;

    const fp = await hotReloadFingerprint();
    if (fp === last) return;

    // Never reload during a live recording — that would destroy the offscreen
    // recorder and lose the call. Leave `last` unchanged so we reload once the
    // recording ends and the change is still pending.
    await loadRecordingState();
    if (recordingState.active) return;

    last = fp;
    console.log("[Sales Genie] source changed — reloading extension");
    await chrome.storage.local.set({ devReloadTabs: true });
    chrome.runtime.reload();
  }, 1500);
}

// After a dev reload, refresh content scripts on IDLE meeting tabs so edits to
// meet.js / content_script.js take effect — but never reload a tab that's in an
// active recorded call (that would interrupt the recording).
async function devPostReloadTabRefresh() {
  if (!DEV_MODE) return;
  const { devReloadTabs } = await chrome.storage.local.get("devReloadTabs");
  if (!devReloadTabs) return;
  await chrome.storage.local.remove("devReloadTabs");
  await loadRecordingState();
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ url: ["*://meet.google.com/*", "*://app.zoom.us/*", "*://app.gong.io/*", "*://teams.microsoft.com/*"] });
  } catch (e) { return; }
  for (const t of tabs) {
    const activeRecorded = recordingState.active && recordingState.tabId === t.id;
    if (!activeRecorded && !stillInCallUrl(t.url)) chrome.tabs.reload(t.id);
  }
}

initHotReload();
devPostReloadTabRefresh();
