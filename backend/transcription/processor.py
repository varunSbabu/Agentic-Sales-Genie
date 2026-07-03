"""Turn raw diarized utterances into a Genie-shaped transcript.

ROLE IDENTIFICATION STRATEGY (multi-signal scoring)
====================================================
Single-signal heuristics fail in known cases:
  - "Most questions = Rep" breaks on demo / technical validation calls where
    the prospect asks more questions than the rep
  - "First speaker = Rep" breaks on inbound calls where the prospect initiates

We score each speaker on FOUR weighted signals:

  1. OPENING PATTERNS (weight 3, only counted in their first 3 utterances)
     Reps have scripted openings: "thank you for calling", "my name is",
     "this is X from Y", "how can I help". Prospects almost never use these.

  2. SALES VOCABULARY (weight 1 per match, any position)
     Service language: "let me walk you through", "does that make sense",
     "we offer / provide", "absolutely". Sticky markers of the seller side.

  3. QUESTION COUNT (weight 0.5 per question)
     Useful but not decisive — reps OR prospects can ask many questions.

  4. FIRST-SPEAKER (weight 1, tiebreaker only)
     Slight lean — reps usually initiate outbound calls.

Confidence = (winner_score − runner_up_score) / max(winner_score, 1).
Below 0.3 we flag "low confidence — please verify" in the UI so the user
knows to use the manual override.

All non-Rep speakers collapse into one Prospect role (handles AssemblyAI's
tendency to over-segment a single speaker into multiple IDs, e.g. when the
prospect's voice changes cadence to read a credit-card number).

Callers can bypass detection entirely with `rep_speaker_hint=`.
"""

import re
from dataclasses import dataclass

from backend.transcription.assemblyai_client import Utterance


# ---------------------------------------------------------------------------
# Patterns that signal a Rep speaker. All matched case-insensitively.
# Keep these short and English-centric — the dev console targets English calls.
# ---------------------------------------------------------------------------
_REP_OPENING_PATTERNS = [
    re.compile(r"\bthank(?:s| you) for (?:calling|taking the time|joining)", re.I),
    re.compile(r"\bmy name is\b", re.I),
    re.compile(r"\bthis is .{1,40}? (?:from|with|calling from)\b", re.I),
    re.compile(r"\bhow (?:can|may) i help you", re.I),
    re.compile(r"\b(?:good )?(?:morning|afternoon|evening)\b.{0,20}\bthis is\b", re.I),
    re.compile(r"\bappreciate you (?:taking the time|joining)", re.I),
]

_SALES_VOCAB_PATTERNS = [
    re.compile(r"\bhow can i help\b", re.I),
    re.compile(r"\blet me (?:show|walk|explain|check|grab|pull up)", re.I),
    re.compile(r"\bwe (?:offer|provide|help|enable|build|specialize)", re.I),
    re.compile(r"\bour (?:product|platform|solution|customers|team|approach)", re.I),
    re.compile(r"\bdoes that (?:make sense|help|answer|sound)", re.I),
    re.compile(r"\b(?:any|other) questions\??\b", re.I),
    re.compile(r"\bwould you (?:like|want) (?:to|me)", re.I),
    re.compile(r"\bif (?:we|i) (?:could|were to) show you", re.I),
    re.compile(r"\bi'?d (?:be )?(?:happy|love) to\b", re.I),
]


@dataclass
class RepDetectionResult:
    rep_label: str
    confidence: float  # 0.0 (tied) ... 1.0 (dominant)
    scores: dict[str, float]
    signals: dict[str, dict[str, int]]  # speaker → {opening: n, sales_vocab: n, ...}


@dataclass
class GenieTranscript:
    """Output of the processor — what the LangGraph agent consumes."""

    speakers: list[dict]
    formatted_text: str
    talk_ratio_rep: float
    talk_ratio_prospect: float
    question_count: int
    speaker_count: int  # logical: 1 (rep only) or 2 (rep + prospect)
    raw_speaker_count: int  # what AssemblyAI returned — useful for diagnostics
    rep_speaker_label: str  # which raw label (A/B/C) we mapped to Rep
    role_map: dict[str, str]  # raw_label → "Rep" | "Prospect"
    rep_detection_confidence: float  # 0.0 = tied; 1.0 = clear winner
    rep_detection_signals: dict[str, dict[str, int]]  # per-speaker signal counts
    rep_detection_overridden: bool  # True if caller passed rep_speaker_hint

    def to_dict(self) -> dict:
        return {
            "speakers": self.speakers,
            "formatted_text": self.formatted_text,
            "talk_ratio_rep": self.talk_ratio_rep,
            "talk_ratio_prospect": self.talk_ratio_prospect,
            "question_count": self.question_count,
            "speaker_count": self.speaker_count,
            "raw_speaker_count": self.raw_speaker_count,
            "rep_speaker_label": self.rep_speaker_label,
            "role_map": self.role_map,
            "rep_detection_confidence": self.rep_detection_confidence,
            "rep_detection_signals": self.rep_detection_signals,
            "rep_detection_overridden": self.rep_detection_overridden,
        }


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _distinct_speakers_in_order(utterances: list[Utterance]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in utterances:
        if u.speaker not in seen:
            seen.add(u.speaker)
            out.append(u.speaker)
    return out


def _count_pattern_hits(text: str, patterns: list[re.Pattern]) -> int:
    return sum(len(p.findall(text)) for p in patterns)


def detect_rep(utterances: list[Utterance]) -> RepDetectionResult:
    """Score each speaker on multiple Rep-indicating signals, pick the winner.

    Returned `confidence` is (top - second) / max(top, 1), so 0.0 means a tie
    and 1.0 means the winner dominated. The UI flags anything below 0.3 as
    "low confidence — please verify".
    """
    speakers = _distinct_speakers_in_order(utterances)
    if not speakers:
        return RepDetectionResult(rep_label="", confidence=0.0, scores={}, signals={})
    if len(speakers) == 1:
        return RepDetectionResult(
            rep_label=speakers[0], confidence=1.0,
            scores={speakers[0]: 1.0},
            signals={speakers[0]: {}},
        )

    # Bucket utterances per speaker and track their order of appearance
    per_speaker: dict[str, list[tuple[int, str]]] = {s: [] for s in speakers}
    for idx, u in enumerate(utterances):
        per_speaker.setdefault(u.speaker, []).append((idx, u.text))

    scores: dict[str, float] = {}
    signals: dict[str, dict[str, int]] = {}
    for sp, texts in per_speaker.items():
        if not texts:
            scores[sp] = 0.0
            signals[sp] = {}
            continue

        # 1. Opening patterns — count only in the speaker's first 3 utterances.
        opening_hits = sum(
            _count_pattern_hits(text, _REP_OPENING_PATTERNS)
            for _, text in texts[:3]
        )
        # 2. Sales vocabulary anywhere in their utterances.
        sales_hits = sum(
            _count_pattern_hits(text, _SALES_VOCAB_PATTERNS) for _, text in texts
        )
        # 3. Question count across all their utterances.
        questions = sum(text.count("?") for _, text in texts)
        # 4. First-speaker tiebreaker: 1 point if their first utterance is
        #    the very first turn overall.
        first_overall = 1 if texts[0][0] == 0 else 0

        score = (3 * opening_hits) + (1 * sales_hits) + (0.5 * questions) + first_overall
        scores[sp] = score
        signals[sp] = {
            "opening_hits": opening_hits,
            "sales_vocab_hits": sales_hits,
            "questions": questions,
            "first_speaker": first_overall,
        }

    # Pick winner; confidence = how dominant the winner is over runner-up
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    rep_label, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = max(0.0, (top - second) / max(top, 1.0))

    # Floor: if no signals fired anywhere, fall back to first-to-speak
    if top == 0:
        rep_label = speakers[0]
        confidence = 0.0

    return RepDetectionResult(
        rep_label=rep_label,
        confidence=round(confidence, 3),
        scores={k: round(v, 2) for k, v in scores.items()},
        signals=signals,
    )


def identify_roles(
    utterances: list[Utterance],
    *,
    rep_speaker_hint: str | None = None,
) -> tuple[dict[str, str], RepDetectionResult, bool]:
    """Return (role_map, detection_result, overridden).

    `role_map` always collapses to exactly two logical roles: one Rep,
    everyone else Prospect. Set `rep_speaker_hint` to bypass detection.
    """
    if not utterances:
        return {}, RepDetectionResult("", 0.0, {}, {}), False

    speakers = _distinct_speakers_in_order(utterances)
    if len(speakers) == 1:
        det = RepDetectionResult(speakers[0], 1.0, {speakers[0]: 1.0}, {speakers[0]: {}})
        return {speakers[0]: "Rep"}, det, bool(rep_speaker_hint)

    # Always compute detection result for diagnostics
    detection = detect_rep(utterances)

    # Explicit override wins if it names a real speaker
    if rep_speaker_hint and rep_speaker_hint in speakers:
        return (
            {s: ("Rep" if s == rep_speaker_hint else "Prospect") for s in speakers},
            detection,
            True,
        )

    rep = detection.rep_label
    return {s: ("Rep" if s == rep else "Prospect") for s in speakers}, detection, False


def format_transcript(
    utterances: list[Utterance], roles: dict[str, str]
) -> str:
    return "\n".join(
        f"{roles.get(u.speaker, u.speaker)}: {u.text}" for u in utterances
    )


def calculate_talk_ratios(
    utterances: list[Utterance], roles: dict[str, str]
) -> tuple[float, float]:
    rep_words = 0
    prospect_words = 0
    for u in utterances:
        wc = len(u.text.split())
        role = roles.get(u.speaker, "")
        if role == "Rep":
            rep_words += wc
        elif role == "Prospect":
            prospect_words += wc
    total = rep_words + prospect_words
    if total == 0:
        return 0.0, 0.0
    return (
        round(rep_words / total * 100, 1),
        round(prospect_words / total * 100, 1),
    )


def count_rep_questions(
    utterances: list[Utterance], roles: dict[str, str]
) -> int:
    """Sentences ending in '?' attributed to the Rep role."""
    count = 0
    for u in utterances:
        if roles.get(u.speaker) != "Rep":
            continue
        sentences = _SENTENCE_SPLIT.split(u.text)
        for s in sentences:
            if s.rstrip().endswith("?"):
                count += 1
    return count


def process_utterances(
    utterances: list[Utterance],
    *,
    rep_speaker_hint: str | None = None,
) -> GenieTranscript:
    """End-to-end: roles → formatted text → talk ratios → question count."""
    raw_count = len({u.speaker for u in utterances})

    if not utterances:
        return GenieTranscript(
            speakers=[], formatted_text="",
            talk_ratio_rep=0.0, talk_ratio_prospect=0.0,
            question_count=0, speaker_count=0, raw_speaker_count=0,
            rep_speaker_label="", role_map={},
            rep_detection_confidence=0.0,
            rep_detection_signals={},
            rep_detection_overridden=False,
        )

    roles, detection, overridden = identify_roles(
        utterances, rep_speaker_hint=rep_speaker_hint
    )
    rep_label = next((s for s, r in roles.items() if r == "Rep"), "")
    rep_ratio, prospect_ratio = calculate_talk_ratios(utterances, roles)
    speakers = [
        {
            "speaker": u.speaker,
            "role": roles.get(u.speaker, u.speaker),
            "text": u.text,
            "start_ms": u.start_ms,
            "end_ms": u.end_ms,
            "confidence": u.confidence,
        }
        for u in utterances
    ]
    logical_speaker_count = len({r for r in roles.values()})

    return GenieTranscript(
        speakers=speakers,
        formatted_text=format_transcript(utterances, roles),
        talk_ratio_rep=rep_ratio,
        talk_ratio_prospect=prospect_ratio,
        question_count=count_rep_questions(utterances, roles),
        speaker_count=logical_speaker_count,
        raw_speaker_count=raw_count,
        rep_speaker_label=rep_label,
        role_map=dict(roles),
        rep_detection_confidence=detection.confidence,
        rep_detection_signals=detection.signals,
        rep_detection_overridden=overridden,
    )
