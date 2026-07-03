// =============================================================================
// Sales Genie — settings / options page controller
// =============================================================================

const $ = (id) => document.getElementById(id);
const DEFAULT_BACKEND = "http://localhost:8000";
let backendUrl = DEFAULT_BACKEND;

function sw(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) => resolve(r || {})));
}
async function getToken() { return (await sw({ type: "GET_AUTH_TOKEN" })).token; }

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
    err.status = res.status; throw err;
  }
  return data;
}
function setStatus(id, kind, text) { const el = $(id); el.className = `status show ${kind}`; el.textContent = text; }
function esc(s) { return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

// ---------------------------------------------------------------------------
async function checkConn() {
  try {
    const r = await fetch(`${backendUrl}/health`);
    if (r.ok) { const d = await r.json(); $("s-conn-dot").className = "dot ok"; $("s-conn-text").textContent = `up · ${d.env}`; return true; }
  } catch (e) {}
  $("s-conn-dot").className = "dot err"; $("s-conn-text").textContent = "offline"; return false;
}

// ---------------------------------------------------------------------------
// 1. Backend + account
// ---------------------------------------------------------------------------
$("s-save-backend").onclick = async () => {
  backendUrl = ($("s-backend").value || DEFAULT_BACKEND).trim().replace(/\/$/, "");
  await chrome.storage.local.set({ backendUrl });
  await checkConn();
  setStatus("s-account-status", "ok", "✓ backend saved");
  loadAll();
};

async function loadAccount() {
  const token = await getToken();
  if (!token) { $("s-account").innerHTML = 'Not logged in — open the extension popup to sign in.'; return false; }
  try {
    const me = await api("/auth/me");
    $("s-account").innerHTML = `Logged in as <strong>${esc(me.email)}</strong> (${esc(me.full_name)})`;
    return true;
  } catch (e) {
    $("s-account").innerHTML = `Auth error: ${esc(e.message)}`;
    return false;
  }
}

// ---------------------------------------------------------------------------
// 2. Knowledge base
// ---------------------------------------------------------------------------
async function refreshKb() {
  try {
    const d = await api("/config/kb/status");
    if (!d.documents.length) { $("s-kb-list").textContent = "No documents uploaded yet."; return; }
    $("s-kb-list").innerHTML = d.documents.map((doc) => {
      const color = doc.status === "ready" ? "var(--green)" : doc.status === "failed" ? "var(--red)" : "var(--amber)";
      return `<div style="display:flex;gap:8px;align-items:center;padding:2px 0;">
        <span style="flex:1;">${esc(doc.filename)}</span>
        <span style="color:${color};font-size:11px;">${doc.status}</span>
        <span class="muted" style="font-size:11px;">${doc.chunk_count} chunks</span>
        <button class="secondary" style="width:auto;padding:2px 8px;font-size:11px;" data-del="${doc.id}">del</button>
      </div>`;
    }).join("") + `<div class="muted" style="margin-top:6px;">${d.total_chunks} chunks total</div>`;
    $("s-kb-list").querySelectorAll("[data-del]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("Delete this document + its vectors?")) return;
        try { await api(`/config/kb/${b.dataset.del}`, { method: "DELETE" }); refreshKb(); }
        catch (e) { setStatus("s-kb-status", "err", e.message); }
      };
    });
  } catch (e) {
    $("s-kb-list").textContent = `Couldn't load KB: ${e.message}`;
  }
}
$("s-kb-refresh").onclick = refreshKb;
$("s-kb-upload").onclick = async () => {
  const f = $("s-kb-file").files[0];
  if (!f) { setStatus("s-kb-status", "warn", "pick a file"); return; }
  const token = await getToken();
  setStatus("s-kb-status", "info", `uploading ${f.name}…`);
  const form = new FormData(); form.append("file", f);
  try {
    const res = await fetch(`${backendUrl}/config/kb/upload`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form });
    const d = await res.json();
    if (!res.ok) { setStatus("s-kb-status", "err", d.detail || "upload failed"); return; }
    setStatus("s-kb-status", "ok", `✓ ${f.name} accepted — embedding`);
    $("s-kb-file").value = "";
    for (let i = 0; i < 15; i++) { await sleep(2000); await refreshKb(); }
  } catch (e) { setStatus("s-kb-status", "err", e.message); }
};

// ---------------------------------------------------------------------------
// 3. Integrations
// ---------------------------------------------------------------------------
$("s-save-notion").onclick = async () => {
  try {
    await api("/config/integrations", { method: "POST", body: JSON.stringify({
      notion_token: $("s-notion-token").value, notion_database_id: $("s-notion-db").value }) });
    setStatus("s-integ-status", "ok", "✓ Notion saved"); loadChecklist();
  } catch (e) { setStatus("s-integ-status", "err", e.message); }
};
$("s-test-notion").onclick = async () => {
  try { const r = await api("/config/integrations/test/notion", { method: "POST" });
    setStatus("s-integ-status", r.ok ? "ok" : "err", `${r.ok?"✓":"✗"} notion: ${r.detail||r.error}`); }
  catch (e) { setStatus("s-integ-status", "err", e.message); }
};
$("s-save-sheets").onclick = async () => {
  try {
    await api("/config/integrations", { method: "POST", body: JSON.stringify({
      sheets_id: $("s-sheets-id").value, sheets_credentials: $("s-sheets-creds").value }) });
    setStatus("s-integ-status", "ok", "✓ Sheets saved"); loadChecklist();
  } catch (e) { setStatus("s-integ-status", "err", e.message); }
};
$("s-test-sheets").onclick = async () => {
  try { const r = await api("/config/integrations/test/sheets", { method: "POST" });
    setStatus("s-integ-status", r.ok ? "ok" : "err", `${r.ok?"✓":"✗"} sheets: ${r.detail||r.error}`); }
  catch (e) { setStatus("s-integ-status", "err", e.message); }
};

// ---------------------------------------------------------------------------
// 4. Notifications / preferences
// ---------------------------------------------------------------------------
async function loadPrefs() {
  try {
    const p = await api("/config/preferences");
    $("s-manager-email").value = p.manager_email || "";
    $("s-low").value = p.alert_threshold_low ?? 2.5;
    $("s-high").value = p.alert_threshold_high ?? 4.0;
    $("s-notify-email").checked = !!p.notify_email;
  } catch (e) {}
}
$("s-save-prefs").onclick = async () => {
  try {
    const p = await api("/config/preferences", { method: "PUT", body: JSON.stringify({
      manager_email: $("s-manager-email").value.trim(),
      alert_threshold_low: parseFloat($("s-low").value),
      alert_threshold_high: parseFloat($("s-high").value),
      notify_email: $("s-notify-email").checked }) });
    setStatus("s-notif-status", "ok", `✓ saved (${p.alert_threshold_low}/${p.alert_threshold_high})`);
  } catch (e) { setStatus("s-notif-status", "err", e.message); }
};
$("s-test-email").onclick = async () => {
  setStatus("s-notif-status", "info", "sending test email…");
  try { const r = await api("/notifications/test/email", { method: "POST", body: JSON.stringify({}) });
    setStatus("s-notif-status", r.ok ? "ok" : "err", r.ok ? `✓ sent to ${r.recipient}` : `✗ ${r.error}`); }
  catch (e) { setStatus("s-notif-status", "err", e.message); }
};

// ---------------------------------------------------------------------------
// 5. Ready checklist
// ---------------------------------------------------------------------------
async function loadChecklist() {
  const items = [];
  const loggedIn = await getToken();
  items.push(["Logged in", !!loggedIn]);
  const connOk = await checkConn();
  items.push(["Backend reachable", connOk]);
  try {
    const kb = await api("/config/kb/status");
    items.push([`Knowledge base (${kb.total_chunks} chunks)`, kb.total_chunks > 0]);
  } catch (e) { items.push(["Knowledge base", false]); }
  try {
    const integ = await api("/config/integrations");
    items.push([`Connectors: ${integ.active_connectors.join(", ")}`, integ.active_connectors.length > 0]);
  } catch (e) { items.push(["Connectors", false]); }
  try {
    const p = await api("/config/preferences");
    items.push(["Email notifications " + (p.notify_email ? "on" : "off"), p.notify_email]);
  } catch (e) {}
  $("s-checklist").innerHTML = items.map(([label, ok]) =>
    `<li class="${ok?'is-ok':'is-no'}"><span class="ck">${ok?'✓':'○'}</span> ${esc(label)}</li>`).join("");
}

// ---------------------------------------------------------------------------
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function loadAll() {
  const ok = await loadAccount();
  if (ok) { await refreshKb(); await loadPrefs(); await loadIntegrations(); }
  await loadChecklist();
}
async function loadIntegrations() {
  try {
    const integ = await api("/config/integrations");
    if (integ.notion_database_id_set) $("s-notion-db").placeholder = "•••• (set)";
    if (integ.sheets_id_set) $("s-sheets-id").placeholder = "•••• (set)";
  } catch (e) {}
}

async function boot() {
  const cfg = await sw({ type: "GET_BACKEND_URL" });
  backendUrl = cfg.backendUrl || DEFAULT_BACKEND;
  $("s-backend").value = backendUrl;
  await loadAll();
}
boot();
