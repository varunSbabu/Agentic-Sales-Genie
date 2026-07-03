// Gong platform extractor. Gong is mostly a call-review platform, so the
// transcript is often already present on the page — grab it when available.
(function () {
  window.SG_EXTRACTORS = window.SG_EXTRACTORS || {};
  window.SG_EXTRACTORS.gong = {
    name: "Gong",
    isInCall() {
      return (
        !!document.querySelector(".call-page") ||
        !!document.querySelector('[class*="call-player"]') ||
        !!document.querySelector('[data-testid*="call"]')
      );
    },
    meetingTitle() {
      const el = document.querySelector('[class*="call-title"], h1[class*="title"]');
      return el ? el.textContent.trim() : "";
    },
    transcript() {
      // Gong renders transcript monologues with speaker labels
      const blocks = document.querySelectorAll('[class*="monologue"], [data-testid*="transcript"] [class*="text"]');
      if (!blocks.length) return "";
      const lines = [];
      blocks.forEach((b) => {
        const speaker = b.querySelector('[class*="speaker"], [class*="name"]');
        const text = b.querySelector('[class*="text"], [class*="content"]') || b;
        const s = speaker ? speaker.textContent.trim() : "";
        const t = text ? text.textContent.trim() : "";
        if (t) lines.push(s ? `${s}: ${t}` : t);
      });
      return lines.join("\n");
    },
  };
})();
