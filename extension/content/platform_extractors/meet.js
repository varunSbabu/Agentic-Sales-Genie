// Google Meet platform extractor.
(function () {
  window.SG_EXTRACTORS = window.SG_EXTRACTORS || {};
  window.SG_EXTRACTORS.meet = {
    name: "Google Meet",
    isInCall() {
      // The Leave button exists ONLY during an active call, so it's the single
      // reliable marker. The old code also matched [data-call-ended] and a broad
      // [jsname][data-is-muted], which linger on the "You left the meeting" /
      // lobby screens — that false-positive kept the call looking live forever,
      // so it never auto-stopped. Match leave-call/leave-meeting only.
      return !!document.querySelector(
        '[aria-label*="Leave call" i], [aria-label*="Leave meeting" i], [aria-label*="Leave" i][role="button"]'
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
