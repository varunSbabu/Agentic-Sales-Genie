// Generic fallback extractor — used when the host doesn't match a known
// platform but the user still wants to record whatever audio the tab plays
// (e.g. Microsoft Teams, or an unknown web conferencing tool).
(function () {
  window.SG_EXTRACTORS = window.SG_EXTRACTORS || {};
  window.SG_EXTRACTORS.generic = {
    name: "Generic",
    isInCall() {
      // Heuristic: any media element currently playing, or common call controls
      const media = Array.from(document.querySelectorAll("audio, video"));
      const playing = media.some((m) => !m.paused && !m.ended && m.currentTime > 0);
      const controls =
        !!document.querySelector('[aria-label*="mute" i], [aria-label*="leave" i], [aria-label*="hang up" i]');
      return playing || controls;
    },
    meetingTitle() {
      return (document.title || "").trim();
    },
    transcript() {
      return "";
    },
  };

  // Microsoft Teams — treated as a named variant of generic since its DOM is volatile
  window.SG_EXTRACTORS.teams = {
    name: "Microsoft Teams",
    isInCall() {
      return (
        !!document.querySelector(".ts-calling-screen") ||
        !!document.querySelector('[data-tid*="call"]') ||
        !!document.querySelector('[aria-label*="Leave" i]')
      );
    },
    meetingTitle() {
      const el = document.querySelector('[data-tid*="title"], [class*="meeting-title"]');
      return el ? el.textContent.trim() : (document.title || "").trim();
    },
    transcript() {
      return "";
    },
  };
})();
