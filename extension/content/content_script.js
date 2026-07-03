// =============================================================================
// Sales Genie — content script
//
// Runs on Zoom / Meet / Gong / Teams. Detects whether a call is active using
// the per-platform extractors (loaded before this file via manifest order),
// and notifies the service worker when a call starts / ends. Also surfaces any
// already-present transcript on review pages.
// =============================================================================

const PLATFORMS = {
  "app.zoom.us": { key: "zoom", name: "Zoom" },
  "meet.google.com": { key: "meet", name: "Google Meet" },
  "app.gong.io": { key: "gong", name: "Gong" },
  "teams.microsoft.com": { key: "teams", name: "Microsoft Teams" },
};

function currentPlatform() {
  const host = location.hostname;
  for (const domain in PLATFORMS) {
    if (host.includes(domain)) return PLATFORMS[domain];
  }
  return null;
}

function getExtractor(key) {
  const ex = window.SG_EXTRACTORS || {};
  return ex[key] || ex.generic || null;
}

let lastInCall = false;
let pollTimer = null;

function poll() {
  const platform = currentPlatform();
  if (!platform) return;
  const extractor = getExtractor(platform.key);
  if (!extractor) return;

  let inCall = false;
  try {
    inCall = !!extractor.isInCall();
  } catch (e) {
    inCall = false;
  }

  if (inCall && !lastInCall) {
    lastInCall = true;
    let title = "";
    let transcript = "";
    try { title = extractor.meetingTitle ? extractor.meetingTitle() : ""; } catch (e) {}
    try { transcript = extractor.transcript ? extractor.transcript() : ""; } catch (e) {}
    chrome.runtime.sendMessage({
      type: "CALL_DETECTED",
      platform: platform.name,
      platformKey: platform.key,
      title,
      hasTranscript: !!transcript,
    });
  } else if (!inCall && lastInCall) {
    lastInCall = false;
    chrome.runtime.sendMessage({ type: "CALL_ENDED", platform: platform.name });
  }
}

// Let the sidebar ask the content script directly for the current page state
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "PROBE_PAGE") {
    const platform = currentPlatform();
    const extractor = platform ? getExtractor(platform.key) : null;
    let inCall = false, title = "", transcript = "";
    if (extractor) {
      try { inCall = !!extractor.isInCall(); } catch (e) {}
      try { title = extractor.meetingTitle ? extractor.meetingTitle() : ""; } catch (e) {}
      try { transcript = extractor.transcript ? extractor.transcript() : ""; } catch (e) {}
    }
    sendResponse({
      platform: platform ? platform.name : null,
      platformKey: platform ? platform.key : null,
      inCall,
      title,
      transcript,
    });
    return true;
  }
  return false;
});

// Start polling (SPAs mutate the DOM heavily, so poll rather than one-shot)
pollTimer = setInterval(poll, 3000);
poll();
