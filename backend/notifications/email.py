"""SendGrid email delivery for alert notifications.

Executive-summary format — brief, scannable, action-oriented:

  1. Meeting header  — call title, rep, who the call was with, type, duration
  2. Score at a glance — big band label + small numeric + next-step quality
  3. Reasoning (3-4 bullets)  — the WHY behind the alert
  4. Recommended next steps (2-3 bullets) — concrete manager actions
  5. CTA button → View Full Analysis (dimension detail lives there, not here)

Managers should be able to read this on a phone in under 30 seconds and
decide what to do. Full analysis (dimension scores, evidence quotes,
objections detail, buying signals) is one click away.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Iterable

from backend.config import settings
from backend.utils.logging import logger


class EmailDeliveryError(Exception):
    """Raised when SendGrid rejects the send."""


@dataclass
class AlertEmailPayload:
    """Everything the templates need. Only what the exec-summary body actually renders."""

    recipient_email: str
    rep_name: str
    rep_email: str

    # Meeting header
    call_title: str = ""
    prospect_name: str = ""
    call_type: str = ""
    duration_secs: int = 0

    # Score at a glance
    overall_score: float = 0.0
    score_band: str = ""
    score_justification: str = ""
    next_step_quality: str = ""

    # Narrative
    ai_summary: str = ""
    call_notes: str = ""
    call_summary_bullets: list[str] = field(default_factory=list)

    # Full detail for the professional overview
    dimension_scores: list[dict] = field(default_factory=list)
    dimension_scores_count: int = 0
    key_quotes: list = field(default_factory=list)  # str now; dict for legacy rows

    # Reasoning inputs
    strengths: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    loss_risk_categories: list[str] = field(default_factory=list)
    objections: list[dict] = field(default_factory=list)
    buying_signals: list[dict] = field(default_factory=list)
    competitors_mentioned: list[dict] = field(default_factory=list)

    # Next step
    next_step_action: str = ""
    next_step_owner: str = ""

    analysis_url: str = ""


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
S = {
    "table": (
        "width:100%; max-width:560px; margin:0 auto; "
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; "
        "background:#ffffff; border-collapse:collapse;"
    ),
    "banner_red": "background:#B00020; color:#ffffff; padding:16px 24px; font-size:18px; font-weight:600;",
    "banner_green": "background:#1B6B3A; color:#ffffff; padding:16px 24px; font-size:18px; font-weight:600;",
    "meta_row": "background:#f5f5f7; padding:12px 24px; font-size:13px; color:#3a3a3a; line-height:1.6;",
    "body": "padding:20px 24px; color:#202020; font-size:14px; line-height:1.55;",
    "score_block_red": (
        "background:#FDECEE; border-radius:6px; padding:14px 16px; margin:0 0 16px; "
        "border-left:4px solid #B00020;"
    ),
    "score_block_green": (
        "background:#EAF5EE; border-radius:6px; padding:14px 16px; margin:0 0 16px; "
        "border-left:4px solid #1B6B3A;"
    ),
    "score_number": "font-size:24px; font-weight:700; line-height:1;",
    "h4": "margin:16px 0 6px; font-size:12px; color:#666666; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;",
    "h4_first": "margin:0 0 6px; font-size:12px; color:#666666; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;",
    "li_bullet": "margin:0 0 6px; padding-left:20px; position:relative; color:#333333;",
    "cta": (
        "display:inline-block; background:#2f81f7; color:#ffffff; "
        "padding:10px 20px; text-decoration:none; border-radius:6px; "
        "font-weight:500; font-size:14px;"
    ),
    "footer": "padding:12px 24px; background:#fafafa; color:#888888; font-size:11px; text-align:center; border-top:1px solid #eeeeee;",
}


def _e(s) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_duration(secs: int) -> str:
    if not secs or secs <= 0:
        return "—"
    m = secs // 60
    s = secs % 60
    if m == 0:
        return f"{s} sec"
    if s == 0:
        return f"{m} min"
    return f"{m} min {s} sec"


def _bullet(text: str, dot_color: str = "#666666") -> str:
    return (
        f'<div style="{S["li_bullet"]}">'
        f'<span style="position:absolute; left:6px; color:{dot_color};">•</span>'
        f"{_e(text)}"
        f"</div>"
    )


def _cta(url: str, label: str) -> str:
    if not url:
        return ""
    return f'<a href="{_e(url)}" style="{S["cta"]}">{_e(label)}</a>'


def _first_sentences(text: str, max_sentences: int = 2, max_chars: int = 320) -> str:
    """Return the first 1-2 sentences of the AI summary as an excerpt."""
    text = (text or "").strip()
    if not text:
        return ""
    out, count = [], 0
    buf = ""
    for ch in text:
        buf += ch
        if ch in ".!?":
            out.append(buf.strip())
            count += 1
            buf = ""
            if count >= max_sentences:
                break
    excerpt = " ".join(out).strip() or text
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 1].rstrip() + "…"
    return excerpt


def _at_a_glance(p: AlertEmailPayload) -> str:
    """Compact one-line tally of the structured signals — links to detail."""
    chips = []
    if p.dimension_scores_count:
        chips.append(f"{p.dimension_scores_count} dimensions scored")
    if p.buying_signals:
        chips.append(f"{len(p.buying_signals)} buying signal(s)")
    if p.objections:
        unaddressed = sum(1 for o in p.objections if not o.get("was_addressed"))
        chips.append(f"{len(p.objections)} objection(s), {unaddressed} unaddressed")
    if p.loss_risk_categories:
        chips.append(f"{len(p.loss_risk_categories)} loss risk(s)")
    if not chips:
        return ""
    return (
        f'<div style="font-size:11px; color:#888888; margin:0 0 4px;">'
        f'{" &nbsp;·&nbsp; ".join(_e(c) for c in chips)}'
        f' &nbsp;·&nbsp; <span style="color:#888;">full detail in the report ↓</span></div>'
    )


# ---------------------------------------------------------------------------
# Reasoning — the "why this alert fired" bullets
# ---------------------------------------------------------------------------
def _intervention_reasons(p: AlertEmailPayload) -> list[str]:
    """3-4 concise reasons explaining WHY intervention triggered.
    Combines: top improvements + unaddressed objection count + loss risks."""
    reasons: list[str] = []

    # 1st-3rd bullets: the top improvements (already framework-grounded)
    for imp in (p.improvements or [])[:3]:
        # Trim ultra-long improvement text to keep the email skimmable
        reasons.append(imp[:180] + ("…" if len(imp) > 180 else ""))

    # Extra bullet if unaddressed objections
    unaddressed = [o for o in p.objections if not o.get("was_addressed")]
    if unaddressed:
        cats = ", ".join(sorted({(o.get("category") or "OTHER") for o in unaddressed}))[:60]
        reasons.append(f"{len(unaddressed)} objection(s) went unaddressed ({cats})")

    # Extra bullet if loss risks
    if p.loss_risk_categories:
        top_risks = ", ".join(p.loss_risk_categories[:3])
        reasons.append(f"Loss risks flagged: {top_risks}")

    # Cap at 5 bullets total
    return reasons[:5]


def _coaching_reasons(p: AlertEmailPayload) -> list[str]:
    """3-4 concise reasons explaining WHY this call is coaching-example worthy."""
    reasons: list[str] = []

    for s in (p.strengths or [])[:3]:
        reasons.append(s[:180] + ("…" if len(s) > 180 else ""))

    # Bonus: strongest buying signal
    strong = [b for b in p.buying_signals if (b.get("strength") or "").lower() == "strong"]
    if strong:
        cats = ", ".join(sorted({(b.get("category") or "SIGNAL") for b in strong}))[:60]
        reasons.append(f"{len(strong)} strong buying signal(s) captured: {cats}")

    return reasons[:5]


# ---------------------------------------------------------------------------
# Recommended next steps — deterministic
# ---------------------------------------------------------------------------
def _intervention_next_steps(p: AlertEmailPayload) -> list[str]:
    steps: list[str] = []
    steps.append("Listen to the recording end-to-end within 48 hours.")

    if p.improvements:
        steps.append(
            f"Coach on: {p.improvements[0][:160]}{'…' if len(p.improvements[0]) > 160 else ''}"
        )

    unaddressed = [o for o in p.objections if not o.get("was_addressed")]
    if unaddressed:
        steps.append(
            f"Role-play the 5-step objection response for {unaddressed[0].get('category', 'the objection')} "
            "(acknowledge → clarify → isolate → respond → confirm)."
        )
    if p.next_step_quality in ("STALLED", "CREATED_RISK"):
        steps.append(
            "Have the rep send a specific follow-up TODAY with a proposed date and agenda."
        )
    return steps[:4]


def _coaching_next_steps(p: AlertEmailPayload) -> list[str]:
    steps = ["Share this recording in the next team standup."]
    if p.strengths:
        steps.append("Save the highlighted moments as a coaching example.")
    steps.append(f"Recognize {p.rep_name or 'the rep'} publicly in your team channel.")
    return steps


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def _meta_line(p: AlertEmailPayload) -> str:
    """Compact single-line meeting metadata: title + rep + prospect + type + duration."""
    parts = []
    if p.rep_name:
        parts.append(f"<strong>Rep:</strong> {_e(p.rep_name)}")
    with_label = p.prospect_name or "(prospect not named on call)"
    parts.append(f"<strong>With:</strong> {_e(with_label)}")
    if p.call_type:
        parts.append(f"<strong>Type:</strong> {_e(p.call_type)}")
    parts.append(f"<strong>Duration:</strong> {_fmt_duration(p.duration_secs)}")
    return " · ".join(parts)


def _score_block(p: AlertEmailPayload, is_intervention: bool) -> str:
    color = "#B00020" if is_intervention else "#1B6B3A"
    style = S["score_block_red"] if is_intervention else S["score_block_green"]
    next_step_line = (
        f' · Next step: <strong style="color:{color};">{_e(p.next_step_quality or "—")}</strong>'
        if p.next_step_quality
        else ""
    )
    justif = (
        f'<div style="font-size:12px; color:#555555; font-style:italic; margin-top:6px;">{_e(p.score_justification)}</div>'
        if p.score_justification
        else ""
    )
    return (
        f'<div style="{style}">'
        f'<div><span style="{S["score_number"]}; color:{color};">{p.overall_score:.1f}</span>'
        f' <span style="color:#666666; font-size:13px;">/ 5</span>'
        f' &nbsp;·&nbsp; <span style="color:{color}; font-weight:600;">{_e(p.score_band)}</span>'
        f"{next_step_line}</div>"
        f"{justif}"
        f"</div>"
    )


def _reasoning_block(reasons: list[str], color: str) -> str:
    if not reasons:
        return '<p style="color:#888888; font-style:italic;">(no specific reasons captured)</p>'
    return "".join(_bullet(r, color) for r in reasons)


def _next_step_block(steps: list[str], color: str) -> str:
    if not steps:
        return ""
    numbered = ""
    for i, step in enumerate(steps, 1):
        numbered += (
            f'<div style="margin:0 0 6px; padding-left:24px; position:relative; color:#333333;">'
            f'<span style="position:absolute; left:0; color:{color}; font-weight:600;">{i}.</span>'
            f"{_e(step)}</div>"
        )
    return numbered


def _next_step_action_line(p: AlertEmailPayload) -> str:
    """Show what was actually agreed on the call, if anything, as context."""
    if not p.next_step_action:
        return ""
    owner = p.next_step_owner or "(unassigned)"
    return (
        f'<div style="background:#f0f4f8; padding:10px 12px; border-radius:5px; margin:8px 0 0; font-size:12px; color:#3a3a3a;">'
        f'<strong>Committed on the call:</strong> {_e(p.next_step_action)} '
        f'<span style="color:#888;">(owner: {_e(owner)})</span></div>'
    )


def build_intervention_email(p: AlertEmailPayload) -> tuple[str, str]:
    title = p.call_title or f"{p.call_type or 'Sales Call'}"
    with_label = f" ({p.rep_name or 'Rep'} ↔ {p.prospect_name})" if p.prospect_name else ""
    subject = f"🚨 Intervention Required — {title}{with_label}"

    summary_excerpt = _first_sentences(p.ai_summary)
    summary_block = (
        f'<h4 style="{S["h4_first"]}">Summary</h4>'
        f'<p style="font-size:13px; color:#333333; line-height:1.55; margin:0 0 4px;">{_e(summary_excerpt)}</p>'
        f'{_at_a_glance(p)}'
        if summary_excerpt
        else _at_a_glance(p)
    )
    body = f"""
<tr><td style="{S['body']}">
  {_score_block(p, is_intervention=True)}

  {summary_block}

  <h4 style="{S['h4']}">Why this alert fired</h4>
  {_reasoning_block(_intervention_reasons(p), '#B00020')}

  <h4 style="{S['h4']}">Recommended next steps</h4>
  {_next_step_block(_intervention_next_steps(p), '#B00020')}
  {_next_step_action_line(p)}

  <p style="margin:20px 0 0;">{_cta(p.analysis_url, 'View Full Analysis →')}</p>
</td></tr>
""".strip()

    html = f"""
<table style="{S['table']}" cellpadding="0" cellspacing="0">
  <tr><td style="{S['banner_red']}">🚨 {_e(title)}</td></tr>
  <tr><td style="{S['meta_row']}">{_meta_line(p)}</td></tr>
  {body}
  <tr><td style="{S['footer']}">
    Sales Genie · alert fired because score fell below the intervention threshold.
  </td></tr>
</table>
""".strip()
    return subject, html


def build_coaching_email(p: AlertEmailPayload) -> tuple[str, str]:
    title = p.call_title or f"{p.call_type or 'Sales Call'}"
    with_label = f" ({p.rep_name or 'Rep'} ↔ {p.prospect_name})" if p.prospect_name else ""
    subject = f"⭐ Coaching Example — {title}{with_label}"

    summary_excerpt = _first_sentences(p.ai_summary)
    summary_block = (
        f'<h4 style="{S["h4_first"]}">Summary</h4>'
        f'<p style="font-size:13px; color:#333333; line-height:1.55; margin:0 0 4px;">{_e(summary_excerpt)}</p>'
        f'{_at_a_glance(p)}'
        if summary_excerpt
        else _at_a_glance(p)
    )
    body = f"""
<tr><td style="{S['body']}">
  {_score_block(p, is_intervention=False)}

  {summary_block}

  <h4 style="{S['h4']}">What made this call worth sharing</h4>
  {_reasoning_block(_coaching_reasons(p), '#1B6B3A')}

  <h4 style="{S['h4']}">Recommended next steps</h4>
  {_next_step_block(_coaching_next_steps(p), '#1B6B3A')}
  {_next_step_action_line(p)}

  <p style="margin:20px 0 0;">{_cta(p.analysis_url, 'View Full Analysis →')}</p>
</td></tr>
""".strip()

    html = f"""
<table style="{S['table']}" cellpadding="0" cellspacing="0">
  <tr><td style="{S['banner_green']}">⭐ {_e(title)}</td></tr>
  <tr><td style="{S['meta_row']}">{_meta_line(p)}</td></tr>
  {body}
  <tr><td style="{S['footer']}">
    Sales Genie · alert fired because score met the coaching threshold.
  </td></tr>
</table>
""".strip()
    return subject, html


# ---------------------------------------------------------------------------
# SendGrid wrapper (unchanged from before)
# ---------------------------------------------------------------------------
class EmailNotifier:
    def __init__(self) -> None:
        self.api_key = settings.sendgrid_api_key
        self.from_email = settings.sendgrid_from_email
        self.from_name = settings.sendgrid_from_name

    def _require_configured(self) -> None:
        if not self.api_key:
            raise EmailDeliveryError(
                "SENDGRID_API_KEY not set — add it to .env and recreate the container."
            )
        if not self.from_email or self.from_email == "alerts@example.com":
            raise EmailDeliveryError(
                "SENDGRID_FROM_EMAIL not set — must be a verified sender in SendGrid. "
                "Verify one at https://app.sendgrid.com/settings/sender_auth"
            )

    def _send_sync(self, to_email: str, subject: str, html: str) -> dict:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=(self.from_email, self.from_name),
            to_emails=to_email,
            subject=subject,
            html_content=html,
        )
        client = SendGridAPIClient(self.api_key)
        resp = client.send(message)
        return {
            "status_code": resp.status_code,
            "headers": dict(resp.headers) if hasattr(resp, "headers") else {},
        }

    async def send(self, to_email: str, subject: str, html: str) -> dict:
        self._require_configured()
        try:
            result = await asyncio.to_thread(self._send_sync, to_email, subject, html)
            if 200 <= result["status_code"] < 300:
                logger.info(
                    "sendgrid: sent to {} subject={} status={}",
                    to_email, subject, result["status_code"],
                )
                return result
            raise EmailDeliveryError(
                f"SendGrid returned {result['status_code']}: check API key + sender verification"
            )
        except EmailDeliveryError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("sendgrid send failed: {}", exc)
            raise EmailDeliveryError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Professional call-analysis overview — sent for EVERY analyzed call.
# The banner + recommendation adapt to alert_level; the body is the same rich
# overview (summary, dimensions, signals, objections, next steps, key quotes).
# ---------------------------------------------------------------------------
_REC_RED = "background:#FDECEE; border-left:4px solid #B00020; padding:12px 16px; margin:16px 0; color:#202020;"
_REC_GREEN = "background:#EAF5EE; border-left:4px solid #1B6B3A; padding:12px 16px; margin:16px 0; color:#202020;"
_REC_BLUE = "background:#eef4fe; border-left:4px solid #2f81f7; padding:12px 16px; margin:16px 0; color:#202020;"
_BANNER_BLUE = "background:linear-gradient(135deg,#2f81f7,#7c3aed); color:#ffffff; padding:16px 24px; font-size:18px; font-weight:600;"

_LEVEL_THEME = {
    "intervention": {"banner": S["banner_red"], "accent": "#B00020", "label": "🚨 Intervention Required", "rec_style": _REC_RED},
    "coaching":     {"banner": S["banner_green"], "accent": "#1B6B3A", "label": "⭐ Coaching Example", "rec_style": _REC_GREEN},
    "none":         {"banner": _BANNER_BLUE, "accent": "#2f81f7", "label": "📊 Call Analysis", "rec_style": _REC_BLUE},
}


def _dimensions_table(p: AlertEmailPayload) -> str:
    if not p.dimension_scores:
        return ""
    rows = ""
    for d in p.dimension_scores:
        name = _e(d.get("dimension") or "?")
        score = float(d.get("score") or 0)
        mx = float(d.get("max_score") or 5)
        pct = max(0, min(100, (score / mx * 100) if mx else 0))
        color = "#1B6B3A" if score >= 4 else ("#B8860B" if score >= 2.5 else "#B00020")
        rows += (
            f'<tr><td style="padding:6px 0; font-size:13px; color:#333;">{name}</td>'
            f'<td style="padding:6px 0; width:120px;">'
            f'<div style="background:#eee; border-radius:6px; height:8px; width:100px; display:inline-block; vertical-align:middle;">'
            f'<div style="background:{color}; width:{pct:.0f}%; height:8px; border-radius:6px;"></div></div></td>'
            f'<td style="padding:6px 0; text-align:right; font-weight:600; color:{color}; width:60px;">{score:.1f}/{mx:.0f}</td></tr>'
        )
    return f'<h3 style="{S["h4"]}">Dimension scores</h3><table style="width:100%; border-collapse:collapse;">{rows}</table>'


def _signals_list(p: AlertEmailPayload) -> str:
    if not p.buying_signals:
        return ""
    items = ""
    for s in p.buying_signals[:6]:
        strength = _e(s.get("strength") or "medium")
        cat = _e(s.get("category") or "SIGNAL")
        q = _e((s.get("quote") or "")[:160])
        items += f'<div style="{S["li_bullet"]}"><span style="position:absolute;left:4px;color:#1B6B3A;">▲</span><strong>{cat}</strong> <span style="color:#888;font-size:11px;">({strength})</span><br><span style="color:#555;font-style:italic;">"{q}"</span></div>'
    return f'<h3 style="{S["h4"]}">Buying signals</h3>{items}'


def _objections_list(p: AlertEmailPayload) -> str:
    if not p.objections:
        return ""
    items = ""
    for o in p.objections[:6]:
        cat = _e(o.get("category") or "OTHER")
        handled = o.get("was_addressed")
        badge = '<span style="color:#1B6B3A;">✓ addressed</span>' if handled else '<span style="color:#B00020;">✗ unaddressed</span>'
        q = _e((o.get("quote") or "")[:160])
        items += f'<div style="{S["li_bullet"]}"><span style="position:absolute;left:4px;color:#B8860B;">●</span><strong>{cat}</strong> {badge}<br><span style="color:#555;font-style:italic;">"{q}"</span></div>'
    return f'<h3 style="{S["h4"]}">Objections</h3>{items}'


def _competitors_line(p: AlertEmailPayload) -> str:
    if not p.competitors_mentioned:
        return ""
    names = ", ".join(_e(c.get("name") or "?") for c in p.competitors_mentioned)
    return f'<h3 style="{S["h4"]}">Competitors mentioned</h3><p style="margin:0 0 8px; color:#333;">{names}</p>'


def _key_quotes_list(p: AlertEmailPayload) -> str:
    if not p.key_quotes:
        return ""
    items = ""
    for k in p.key_quotes[:4]:
        # key_quotes are plain strings now; tolerate the old {quote,speaker,...}
        # object shape too, for analyses stored before the schema changed.
        if isinstance(k, dict):
            sp = _e(k.get("speaker") or "")
            q = _e((k.get("quote") or "")[:180])
            why = _e((k.get("why_notable") or "")[:120])
        else:
            sp, q, why = "", _e(str(k)[:180]), ""
        sp_block = f'<div style="font-size:11px; color:#888; font-weight:600;">{sp}</div>' if sp else ""
        why_block = f'<div style="font-size:11px; color:#888;">— {why}</div>' if why else ""
        items += (
            f'<div style="border-left:3px solid #ccc; padding:6px 12px; margin:6px 0; background:#fafafa;">'
            f'{sp_block}'
            f'<div style="color:#333; font-style:italic;">"{q}"</div>'
            f'{why_block}</div>'
        )
    return f'<h3 style="{S["h4"]}">Key quotes</h3>{items}'


def _summary_bullets(p: AlertEmailPayload) -> str:
    if not p.call_summary_bullets:
        return ""
    items = "".join(_bullet(b) for b in p.call_summary_bullets[:6])
    return f'<h3 style="{S["h4"]}">At a glance</h3>{items}'


def build_overview_email(p: AlertEmailPayload, alert_level: str) -> tuple[str, str]:
    """Full professional call-analysis overview. Always sent, per-call."""
    theme = _LEVEL_THEME.get(alert_level, _LEVEL_THEME["none"])
    accent = theme["accent"]
    title = p.call_title or (p.call_type or "Sales Call")
    with_label = f" ({p.rep_name or 'Rep'} ↔ {p.prospect_name})" if p.prospect_name else ""

    # Subject reflects the nature of the email
    if alert_level == "intervention":
        subject = f"🚨 Intervention Required — {title}{with_label}"
    elif alert_level == "coaching":
        subject = f"⭐ Coaching Example — {title}{with_label}"
    else:
        subject = f"📊 Call Analysis — {title}{with_label}"

    # Recommendation only for the two alert levels
    recommendation = ""
    if alert_level == "intervention":
        reasons = _intervention_next_steps(p)
        recommendation = f'<div style="{theme["rec_style"]}"><strong>Recommended next steps</strong>{_next_step_block(reasons, accent)}</div>'
    elif alert_level == "coaching":
        reasons = _coaching_next_steps(p)
        recommendation = f'<div style="{theme["rec_style"]}"><strong>Recommended next steps</strong>{_next_step_block(reasons, accent)}</div>'

    summary = _e(p.ai_summary) or "(no summary available)"

    body = f"""
<tr><td style="{S['body']}">
  <div style="background:{accent}14; border-radius:10px; padding:14px; margin:0 0 16px; display:flex; align-items:center;">
    <div style="{S['score_number']}; color:{accent};">{p.overall_score:.1f}<span style="font-size:13px;color:#888;">/5</span></div>
    <div style="margin-left:14px;">
      <div style="font-weight:700; color:{accent};">{_e(p.score_band) or '—'}</div>
      <div style="font-size:12px; color:#555;">Next step: {_e(p.next_step_quality) or '—'}</div>
    </div>
  </div>

  {recommendation}

  <h3 style="{S['h4_first']}">Summary</h3>
  <p style="margin:0 0 8px; color:#333; line-height:1.55;">{summary}</p>

  {_summary_bullets(p)}
  {_dimensions_table(p)}

  <h3 style="{S['h4']}">Strengths</h3>
  {''.join(_bullet(s) for s in p.strengths[:5]) or '<p style="color:#888;">—</p>'}
  <h3 style="{S['h4']}">Improvements</h3>
  {''.join(_bullet(s) for s in p.improvements[:5]) or '<p style="color:#888;">—</p>'}

  {_signals_list(p)}
  {_objections_list(p)}
  {_competitors_line(p)}

  <h3 style="{S['h4']}">Next step</h3>
  {_next_step_action_line(p) or '<p style="color:#888;">Not captured on this call.</p>'}

  {_key_quotes_list(p)}

  <p style="margin:22px 0 0;">{_cta(p.analysis_url, 'View Full Analysis →')}</p>
</td></tr>
""".strip()

    html = f"""
<table style="{S['table']}" cellpadding="0" cellspacing="0">
  <tr><td style="{theme['banner']}">{theme['label']} — {_e(title)}</td></tr>
  <tr><td style="{S['meta_row']}">{_meta_line(p)}</td></tr>
  {body}
  <tr><td style="{S['footer']}">Sales Genie · automated call analysis</td></tr>
</table>
""".strip()
    return subject, html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def send_alert_email(
    payload: AlertEmailPayload, *, alert_level: str
) -> dict:
    """Send the professional overview email for ANY level (none/coaching/intervention)."""
    subject, html = build_overview_email(payload, alert_level)
    result = await EmailNotifier().send(payload.recipient_email, subject, html)
    return {"subject": subject, "recipient": payload.recipient_email, **result}


async def send_test_email(to_email: str, rep_name: str = "Test Rep") -> dict:
    """Send a sample INTERVENTION email demonstrating the new brief format."""
    sample = AlertEmailPayload(
        recipient_email=to_email,
        rep_name=rep_name,
        rep_email=to_email,
        call_title="Nissan Map Update Order",
        prospect_name="John Smith",
        call_type="Service",
        duration_secs=185,
        overall_score=1.8,
        score_band="INTERVENTION REQUIRED",
        score_justification=(
            "Zero discovery, no next-step commitment, and reflexive discounting "
            "in response to affordability — the deal is at high risk of stalling."
        ),
        next_step_quality="STALLED",
        ai_summary=(
            "The rep took an inbound map-update request from John Smith and moved "
            "straight to quoting the $99 price without exploring the customer's "
            "situation. When John hesitated on affordability, the rep offered a "
            "discount rather than understanding the concern, and the call ended "
            "with a vague promise to follow up next month."
        ),
        dimension_scores_count=5,
        strengths=[
            "Rep verified customer identity + account number quickly at the start.",
        ],
        improvements=[
            "Rep skipped discovery entirely and jumped to pricing.",
            "Rep offered a 20% discount as first response to the affordability concern instead of re-anchoring value.",
            "Rep did not confirm a specific next step — 'follow up next month' has no date or commitment.",
        ],
        objections=[
            {"quote": "I'm not sure I can afford it right now",
             "category": "PRICE", "was_addressed": False,
             "how_handled": "Rep pivoted to a discount rather than probing what makes the price feel high."},
        ],
        buying_signals=[],
        loss_risk_categories=["NO_BUDGET_CONFIRMED", "VAGUE_PAIN", "STATUS_QUO_BIAS"],
        next_step_action="Rep will follow up next month",
        next_step_owner="Rep",
        analysis_url="http://localhost:8000/",
    )
    return await send_alert_email(sample, alert_level="intervention")
