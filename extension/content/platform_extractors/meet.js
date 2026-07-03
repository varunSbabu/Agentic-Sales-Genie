// Google Meet platform extractor.
(function () {
  window.SG_EXTRACTORS = window.SG_EXTRACTORS || {};
  window.SG_EXTRACTORS.meet = {
    name: "Google Meet",
    isInCall() {
      // Meet shows call controls (mic/cam/leave) while in a call; the leave
      // button is the most reliable marker.
      return (
        !!document.querySelector('[aria-label*="Leave call" i]') ||
        !!document.querySelector('[data-call-ended]') ||
        !!document.querySelector('[jsname][data-is-muted]')
      );
    },
    meetingTitle() {
      const el = document.querySelector('[data-meeting-title], [data-unresolved-meeting-alias]');
      return el ? el.textContent.trim() : (document.title || "").replace(" - Google Meet", "");
    },
    transcript() {
      // Meet captions container (when captions are on)
      const nodes = document.querySelectorAll('[jsname="tgaKEf"], [class*="caption"] span');
      if (!nodes.length) return "";
      return Array.from(nodes).map((n) => n.textContent.trim()).filter(Boolean).join("\n");
    },
  };
})();
