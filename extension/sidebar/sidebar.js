// =============================================================================
// Sales Genie — sidebar controller (6-state machine)
//   LOGIN → IDLE → CALL_DETECTED → RECORDING → PROCESSING → RESULTS → SAVED
// Talks to the service worker for auth + recording, and directly to the backend
// for login / history / job polling / save.
// =============================================================================

const $ = (id) => document.getElementById(id);
const STATES = ["login", "onboarding", "idle", "detected", "recording", "processing", "results", "saved"];

let backendUrl = "http://localhost:8000";
let recTimer = null;
let recSeconds = 0;
let currentDetected = null;
let lastResult = null;

// ---------------------------------------------------------------------------
// Messaging helpers
// ---------------------------------------------------------------------------
function sw(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) => resolve(r || {})));
}
async function getToken() {
  const r = await sw({ type: "GET_AUTH_TOKEN" });
  return r.token;
}
async function api(path, options = {}) {
  const token = await getToken();
  const headers = Object.assign({}, options.headers || {});
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body && typeof options.body === "string") headers["Content-Type"] = "application/json";
  const res = await fetch(`${backendUrl}${path}`, { ...options, headers });
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = (data && data.detail) || data;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return data;
}

function show(state) {
  STATES.forEach((s) => $(`state-${s}`).classList.toggle("active", s === state));
}
function setStatus(id, kind, text) {
  const el = $(id);
  if (!el) return;
  el.className = `status show ${kind}`;
  el.textContent = text;
}
function clearStatus(id) { const el = $(id); if (el) { el.className = "status"; el.textContent = ""; } }

// ---------------------------------------------------------------------------
// Connection indicator
// ---------------------------------------------------------------------------
async function checkConn() {
  try {
    const res = await fetch(`${backendUrl}/health`);
    if (res.ok) {
      const d = await res.json();
      $("conn-dot").className = "dot ok";
      $("conn-text").textContent = `up · ${d.env}`;
      return true;
    }
  } catch (e) {}
  $("conn-dot").className = "dot err";
  $("conn-text").textContent = "offline";
  return false;
}

// ---------------------------------------------------------------------------
// Boot / routing
// ---------------------------------------------------------------------------
async function boot() {
  const cfg = await sw({ type: "GET_BACKEND_URL" });
  backendUrl = cfg.backendUrl || backendUrl;
  await checkConn();

  const token = await getToken();
  if (!token) { show("login"); return; }

  // If a recording is in progress, resume the RECORDING view
  const st = await sw({ type: "GET_RECORDING_STATE" });
  if (st.state && st.state.active) { resumeRecording(st.state); return; }

  // New / unconfigured users get the guided setup wizard first
  if (await needsOnboarding()) { showOnboarding(); return; }

  // If a call is detected on the active tab, offer to record
  const detected = await probeActiveTab();
  if (detected && detected.inCall) { showDetected(detected); return; }

  showIdle();
}

async function probeActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return null;
    const resp = await new Promise((resolve) =>
      chrome.tabs.sendMessage(tab.id, { type: "PROBE_PAGE" }, (r) => resolve(r))
    );
    if (resp) resp.tabId = tab.id;
    return resp;
  } catch (e) {
    return null; // content script not present on this tab
  }
}

// ---------------------------------------------------------------------------
// LOGIN
// ---------------------------------------------------------------------------
let authMode = "login"; // "login" | "register"

function setAuthMode(mode) {
  authMode = mode;
  const register = mode === "register";
  $("auth-heading").textContent = register ? "Create account" : "Sign in";
  $("auth-sub").textContent = register
    ? "Register a new Sales Genie account."
    : "Log in to your Sales Genie account.";
  $("name-field").style.display = register ? "block" : "none";
  $("btn-login").textContent = register ? "Register" : "Log in";
  $("auth-toggle-text").textContent = register ? "Already have an account?" : "No account?";
  $("auth-toggle").textContent = register ? "Sign in" : "Create one";
  clearStatus("login-status");
}

$("auth-toggle").onclick = (e) => {
  e.preventDefault();
  setAuthMode(authMode === "login" ? "register" : "login");
};

$("btn-login").onclick = async () => {
  clearStatus("login-status");
  const email = $("login-email").value.trim();
  const password = $("login-password").value;
  if (!email || !password) { setStatus("login-status", "warn", "enter email + password"); return; }

  const isRegister = authMode === "register";
  const path = isRegister ? "/auth/register" : "/auth/login";
  const body = { email, password };
  if (isRegister) {
    const name = $("login-name").value.trim();
    if (!name) { setStatus("login-status", "warn", "enter your full name"); return; }
    body.full_name = name;
  }
  setStatus("login-status", "info", isRegister ? "creating account…" : "signing in…");
  try {
    const res = await fetch(`${backendUrl}${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      setStatus("login-status", "err", data.detail || (isRegister ? "registration failed" : "login failed"));
      return;
    }
    await sw({ type: "SAVE_AUTH_TOKEN", token: data.access_token, refresh: data.refresh_token });
    boot();
  } catch (e) {
    setStatus("login-status", "err", e.message);
  }
};
$("btn-open-settings-login").onclick = () => chrome.runtime.openOptionsPage();

// ---------------------------------------------------------------------------
// ONBOARDING — guided first-run setup
// ---------------------------------------------------------------------------
// Onboarding completion is tracked PER USER (keyed by user id), not globally —
// otherwise one configured account would suppress the wizard for every other.
let currentUserId = null;

async function onboardKey() {
  if (!currentUserId) {
    try { const me = await api("/auth/me"); currentUserId = me.id; } catch (e) {}
  }
  return `onboarded_${currentUserId || "unknown"}`;
}

async function needsOnboarding() {
  const key = await onboardKey();
  const stored = await chrome.storage.local.get(key);
  if (stored[key]) return false;
  // An existing user who already has a KB shouldn't be forced through setup.
  try {
    const kb = await api("/config/kb/status");
    if (kb.total_chunks > 0) {
      await chrome.storage.local.set({ [key]: true });
      return false;
    }
  } catch (e) {
    if (e.status === 401) { await sw({ type: "CLEAR_AUTH" }); }
  }
  return true;
}

function updateOnbProgress(kbDone, prefsDone) {
  const segs = [kbDone, prefsDone, true]; // step 3 optional → always "available"
  $("onb-progress").innerHTML = segs
    .map((d, i) => `<div class="seg ${i < 2 && d ? "done" : (i === 2 ? "" : "")}"></div>`)
    .join("");
  $("onb-kb").classList.toggle("done", kbDone);
  $("onb-notif").classList.toggle("done", prefsDone);
  // Finish is enabled once the required step (KB) is done
  $("onb-finish").disabled = !kbDone;
  $("onb-finish").textContent = kbDone ? "Finish setup →" : "Upload a framework to continue";
}

let onbKbDone = false;
let onbPrefsDone = false;

async function onbRefreshKb() {
  try {
    const d = await api("/config/kb/status");
    onbKbDone = d.total_chunks > 0;
    if (d.documents.length) {
      $("onb-kb-list").innerHTML = d.documents.map((doc) => {
        const color = doc.status === "ready" ? "var(--green)" : doc.status === "failed" ? "var(--red)" : "var(--amber)";
        return `<div>• ${escapeHtml(doc.filename)} <span style="color:${color}">${doc.status}</span> (${doc.chunk_count})</div>`;
      }).join("");
    } else {
      $("onb-kb-list").textContent = "No frameworks yet.";
    }
    updateOnbProgress(onbKbDone, onbPrefsDone);
  } catch (e) { /* ignore */ }
}

async function showOnboarding() {
  show("onboarding");
  // Prefill preferences if the account already has some
  try {
    const p = await api("/config/preferences");
    $("onb-manager").value = p.manager_email || "";
    $("onb-low").value = p.alert_threshold_low ?? 2.5;
    $("onb-high").value = p.alert_threshold_high ?? 4.0;
    $("onb-notify").checked = p.notify_email !== false;
  } catch (e) {}
  await onbRefreshKb();
}

$("onb-kb-upload").onclick = async () => {
  const f = $("onb-kb-file").files[0];
  if (!f) { setStatus("onb-kb-status", "warn", "pick a file first"); return; }
  const token = await getToken();
  setStatus("onb-kb-status", "info", `uploading ${f.name}…`);
  const form = new FormData(); form.append("file", f);
  try {
    const res = await fetch(`${backendUrl}/config/kb/upload`, {
      method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form,
    });
    const d = await res.json();
    if (!res.ok) { setStatus("onb-kb-status", "err", d.detail || "upload failed"); return; }
    setStatus("onb-kb-status", "info", "✓ accepted — embedding…");
    $("onb-kb-file").value = "";
    for (let i = 0; i < 12; i++) {
      await sleep(2000);
      await onbRefreshKb();
      if (onbKbDone) { setStatus("onb-kb-status", "ok", "✓ framework ready"); break; }
    }
  } catch (e) { setStatus("onb-kb-status", "err", e.message); }
};

$("onb-save-prefs").onclick = async () => {
  try {
    await api("/config/preferences", { method: "PUT", body: JSON.stringify({
      manager_email: $("onb-manager").value.trim(),
      alert_threshold_low: parseFloat($("onb-low").value) || 2.5,
      alert_threshold_high: parseFloat($("onb-high").value) || 4.0,
      notify_email: $("onb-notify").checked,
    }) });
    onbPrefsDone = true;
    setStatus("onb-notif-status", "ok", "✓ preferences saved");
    updateOnbProgress(onbKbDone, onbPrefsDone);
  } catch (e) { setStatus("onb-notif-status", "err", e.message); }
};

$("onb-open-settings").onclick = () => chrome.runtime.openOptionsPage();

$("onb-finish").onclick = async () => {
  const key = await onboardKey();
  await chrome.storage.local.set({ [key]: true });
  showIdle();
};
$("onb-skip").onclick = async (e) => {
  e.preventDefault();
  const key = await onboardKey();
  await chrome.storage.local.set({ [key]: true });
  showIdle();
};

// ---------------------------------------------------------------------------
// IDLE — recent history
// ---------------------------------------------------------------------------
async function populateAccountChip() {
  try {
    const me = await api("/auth/me");
    currentUserId = me.id;
    const email = me.email || "";
    $("idle-email").textContent = email;
    $("idle-avatar").textContent = (me.full_name || email || "?").trim().charAt(0).toUpperCase();
    // Reflect setup state
    try {
      const kb = await api("/config/kb/status");
      $("idle-setup-state").textContent = kb.total_chunks > 0
        ? `${kb.total_chunks} framework chunks · set up ✓`
        : "no framework yet — tap ✎ to set up";
    } catch (e) { $("idle-setup-state").textContent = ""; }
  } catch (e) {
    if (e.status === 401) { await sw({ type: "CLEAR_AUTH" }); show("login"); }
  }
}

async function showIdle() {
  show("idle");
  populateAccountChip();
  try {
    const data = await api("/history/calls?limit=5");
    if (!data.items.length) {
      $("idle-history").innerHTML = '<p class="muted">No calls yet. Record your first one.</p>';
      return;
    }
    $("idle-history").innerHTML = data.items.map((it) => {
      const band = it.score_band || "unscored";
      const color = bandColor(band);
      const title = it.call_title || it.platform || "Call";
      return `<div class="card" style="margin-top:6px;">
        <div style="display:flex; justify-content:space-between;">
          <span>${escapeHtml(title)}</span>
          <span class="band" style="color:${color}">${it.overall_score != null ? it.overall_score.toFixed(1) : "—"}</span>
        </div>
        <div class="muted">${escapeHtml((it.prospect_name || ""))} · ${band} · ${(it.created_at||"").slice(0,10)}</div>
      </div>`;
    }).join("");
  } catch (e) {
    if (e.status === 401) { await sw({ type: "CLEAR_AUTH" }); show("login"); return; }
    $("idle-history").innerHTML = `<p class="muted">Couldn't load history: ${escapeHtml(e.message)}</p>`;
  }
}
$("btn-open-settings").onclick = () => chrome.runtime.openOptionsPage();
$("btn-run-setup").onclick = () => showOnboarding();
$("btn-logout").onclick = async () => { currentUserId = null; await sw({ type: "CLEAR_AUTH" }); show("login"); };
$("btn-detected-idle").onclick = showIdle;
$("btn-results-idle").onclick = showIdle;
$("btn-saved-idle").onclick = showIdle;

// ---------------------------------------------------------------------------
// CALL_DETECTED
// ---------------------------------------------------------------------------
function showDetected(detected) {
  currentDetected = detected;
  $("detected-platform").textContent = detected.platform || "Call";
  $("detected-title").textContent = detected.title || "";
  clearStatus("detected-status");
  show("detected");
}

$("btn-start-recording").onclick = async () => {
  clearStatus("detected-status");
  setStatus("detected-status", "info", "requesting tab audio…");
  const tabId = currentDetected ? currentDetected.tabId : null;
  const r = await sw({ type: "START_RECORDING", tabId, platform: (currentDetected && currentDetected.platform) || "extension" });
  if (!r.ok) { setStatus("detected-status", "err", r.error || "could not start"); return; }
  resumeRecording(r.state);
};

// ---------------------------------------------------------------------------
// RECORDING
// ---------------------------------------------------------------------------
function resumeRecording(state) {
  show("recording");
  $("rec-platform").textContent = state.platform || "";
  recSeconds = state.startedAt ? Math.floor((Date.now() - state.startedAt) / 1000) : 0;
  updateTimer();
  if (recTimer) clearInterval(recTimer);
  recTimer = setInterval(() => { recSeconds++; updateTimer(); }, 1000);
}
function updateTimer() {
  const m = String(Math.floor(recSeconds / 60)).padStart(2, "0");
  const s = String(recSeconds % 60).padStart(2, "0");
  $("rec-timer").textContent = `${m}:${s}`;
}

$("btn-stop-recording").onclick = async () => {
  if (recTimer) clearInterval(recTimer);
  setStatus("recording-status", "info", "stopping + uploading final audio…");
  const r = await sw({ type: "STOP_RECORDING", durationSecs: recSeconds });
  if (!r.ok) {
    setStatus("recording-status", "err", r.error || "stop failed");
    return;
  }
  startProcessing(r);
};

// ---------------------------------------------------------------------------
// PROCESSING — poll the analysis job
// ---------------------------------------------------------------------------
function markStep(step, cls) {
  const li = document.querySelector(`#proc-steps li[data-step="${step}"]`);
  if (!li) return;
  li.className = cls;
  const icon = li.querySelector(".icon");
  if (icon) icon.textContent = cls === "done" ? "✓" : cls === "active" ? "⟳" : "•";
}

async function startProcessing(stopResult) {
  show("processing");
  markStep("recording", "done");
  markStep("transcribing", "done"); // /recording/stop already transcribed
  markStep("analyzing", "active");
  $("proc-status").textContent = `Transcript ready (${stopResult.utterance_count || 0} utterances). Scoring…`;

  const jobId = stopResult.job_id;
  if (!jobId) { setStatus("proc-status", "err", "no job id returned"); return; }

  for (let i = 0; i < 90; i++) {
    await sleep(2000);
    let job;
    try { job = await api(`/analysis/job/${jobId}`); }
    catch (e) { if (e.status === 404) { $("proc-status").textContent = "job expired"; return; } continue; }

    $("proc-status").textContent = `${job.state} · ${job.progress}% · ${job.step}`;
    if (job.progress >= 90) markStep("saving", "active");
    if (job.state === "done") {
      markStep("analyzing", "done"); markStep("saving", "done");
      lastResult = job.result;
      showResults(job.result);
      return;
    }
    if (job.state === "failed") {
      $("proc-status").className = "status show err";
      $("proc-status").textContent = "✗ " + (job.error || "analysis failed");
      return;
    }
  }
}

// ---------------------------------------------------------------------------
// RESULTS
// ---------------------------------------------------------------------------
function showResults(r) {
  show("results");
  clearStatus("results-status");
  const color = bandColor(r.score_band);
  const rep = Math.round(r.talk_ratio_rep || 0);
  // talk ratios aren't in the job result payload; fall back to 0 if absent
  const strengths = (r.strengths || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  const improvements = (r.improvements || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  const alertColor = r.alert_level === "intervention" ? "var(--red)" : r.alert_level === "coaching" ? "var(--green)" : "var(--muted)";

  $("results-body").innerHTML = `
    ${r.call_title ? `<div style="font-weight:700; font-size:14px; margin-bottom:2px;">${escapeHtml(r.call_title)}</div>` : ""}
    ${r.prospect_name ? `<div class="muted" style="margin-bottom:8px;">with ${escapeHtml(r.prospect_name)}</div>` : ""}
    <div class="score-hero">
      <div class="score-ring" style="background:linear-gradient(135deg, ${color}, ${color}aa)">${(r.overall_score||0).toFixed(1)}</div>
      <div style="flex:1;">
        <div class="band" style="color:${color}">${escapeHtml(r.score_band || "—")}</div>
        <div class="muted">${escapeHtml(r.call_type || "")} · ${escapeHtml(r.next_step_quality || "")}</div>
        <div style="margin-top:5px;"><span class="pill" style="background:${alertColor}22;color:${alertColor}">alert: ${escapeHtml(r.alert_level || "none")}</span></div>
      </div>
    </div>
    ${r.score_justification ? `<div class="muted" style="font-style:italic; line-height:1.45;">${escapeHtml(r.score_justification)}</div>` : ""}
    <div class="section-label">Strengths</div>
    <ul class="list good">${strengths || '<li class="muted">none</li>'}</ul>
    <div class="section-label">Improvements</div>
    <ul class="list warn">${improvements || '<li class="muted">none</li>'}</ul>
    <details>
      <summary>AI summary</summary>
      <div class="muted" style="margin-top:6px; white-space:pre-wrap;">${escapeHtml(r.ai_summary || "")}</div>
    </details>
    <div class="muted" style="margin-top:8px; font-size:10px;">analysis_id: ${r.analysis_id || "—"}</div>
  `;
}

$("btn-save-db").onclick = async () => {
  if (!lastResult || !lastResult.analysis_id) { setStatus("results-status", "warn", "nothing to save"); return; }
  setStatus("results-status", "info", "dispatching to connectors…");
  try {
    const r = await api(`/analysis/save/${lastResult.analysis_id}`, { method: "POST" });
    showSaved(r);
  } catch (e) {
    setStatus("results-status", "err", e.message);
  }
};

$("btn-copy-summary").onclick = async () => {
  if (!lastResult) return;
  const text =
    `${lastResult.call_title || "Call"} — ${(lastResult.overall_score||0).toFixed(1)}/5 (${lastResult.score_band})\n\n` +
    `Strengths:\n${(lastResult.strengths||[]).map((s)=>"• "+s).join("\n")}\n\n` +
    `Improvements:\n${(lastResult.improvements||[]).map((s)=>"• "+s).join("\n")}\n\n` +
    `${lastResult.ai_summary || ""}`;
  try { await navigator.clipboard.writeText(text); setStatus("results-status", "ok", "✓ copied to clipboard"); }
  catch (e) { setStatus("results-status", "err", "copy failed"); }
};

// ---------------------------------------------------------------------------
// SAVED
// ---------------------------------------------------------------------------
function showSaved(saveResult) {
  show("saved");
  const rows = (saveResult.connector_results || []).map((c) => {
    const ok = c.ok ? "✓" : "✗";
    const link = c.external_url ? ` · <a href="${c.external_url}" target="_blank">open</a>` : "";
    return `<div style="font-size:12px; margin-bottom:4px;">${ok} ${escapeHtml(c.connector)} — ${escapeHtml(c.detail || c.error || "")}${link}</div>`;
  }).join("");
  $("saved-body").innerHTML = `
    <div style="margin-bottom:8px;">Analysis dispatched to your connectors.</div>
    ${rows || '<div class="muted">No connectors configured.</div>'}
    <div class="muted" style="margin-top:8px;">Saved at ${new Date().toLocaleTimeString()}</div>
  `;
}

// ---------------------------------------------------------------------------
// utils
// ---------------------------------------------------------------------------
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function escapeHtml(s) { return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function bandColor(band) {
  const b = (band || "").toUpperCase();
  if (b.includes("EXCELLENT")) return "#3fb950";
  if (b.includes("SOLID")) return "#56d364";
  if (b.includes("MIXED")) return "#d29922";
  if (b.includes("INTERVENTION")) return "#f85149";
  return "#8b949e";
}

boot();
