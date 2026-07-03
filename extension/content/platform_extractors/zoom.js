// Zoom platform extractor. Detects an active call and (best-effort) pulls
// participant / title hints from the DOM. Exposed on window.SG_EXTRACTORS.
(function () {
  window.SG_EXTRACTORS = window.SG_EXTRACTORS || {};
  window.SG_EXTRACTORS.zoom = {
    name: "Zoom",
    isInCall() {
      return (
        !!document.querySelector(".video-avatar__avatar") ||
        !!document.querySelector('[class*="meeting-client"]') ||
        !!document.querySelector('[aria-label*="mute" i]')
      );
    },
    meetingTitle() {
      const el = document.querySelector('[class*="meeting-title"], .meeting-info-icon__title');
      return el ? el.textContent.trim() : "";
    },
    // Some Zoom review pages expose a transcript; grab it if present.
    transcript() {
      const nodes = document.querySelectorAll('[class*="transcript"] [class*="text"], .transcript-item');
      if (!nodes.length) return "";
      return Array.from(nodes).map((n) => n.textContent.trim()).join("\n");
    },
  };
})();
