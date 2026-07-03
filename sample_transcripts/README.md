# Sample Transcripts

Speaker-labelled (`Rep:` / `Prospect:`) test transcripts for exercising the
Call Analysis pipeline. Paste any of these into the **Call Analysis** panel of
the dev console (`http://localhost:8000/`) or the Chrome extension.

Scores vary ±0.3 between runs (normal LLM behaviour) — judge by the **band**,
not the exact number.

| File | Scenario | Expected band | Alert | Notable signals |
|------|----------|---------------|-------|-----------------|
| `01_discovery_excellent.txt` | Textbook B2B discovery — quantified pain, timeline, decision team, specific next step | EXCELLENT (~4.2–4.6) | coaching | 3–5 buying signals, ADVANCED next step |
| `02_intervention_poor.txt` | Cold pitch, zero discovery, reflexive discount, no next step | INTERVENTION (~1.5–2.2) | intervention | multiple loss risks, HubSpot competitor |
| `03_objection_heavy.txt` | 4–5 objections (price, security, timing, competitor) | MIXED (~2.8–3.4) | none/borderline | Salesforce as internal preference, CHAMPION_ONLY risk |
| `04_demo_multi_competitor.txt` | Demo vs Highspot + Seismic, rep honest about weakness | SOLID (~3.4–3.8) | none | 2 competitors, strong value tie-ins |
| `05_service_inbound_short.txt` | Short inbound support call + soft upsell | MIXED (~2.5–3.2) | none | Service call type, relaxed discovery rubric |
| `06_nissan_map_update.txt` | Real B2C service/upsell call (map update order) | MIXED (~2.0–3.0) | none/intervention | Commercial, discount-first handling |
| `07_discovery_sdr_outbound.txt` | SDR-team discovery (reply-rate problem) | SOLID/EXCELLENT (~3.8–4.4) | coaching | $170k pain, September deadline, RevOps stakeholder |

## What a correct result looks like

- `dimension_scores` populated (5 for a full framework)
- Evidence quotes appear **verbatim** in the transcript (no hallucinated
  "automation pitch" / "CSV pulls" — those are framework examples, never in output)
- `objections` / `buying_signals` / `competitors_mentioned` match the call
- `next_step_action` + `next_step_owner` reflect what was actually agreed

## Red flags (report if seen)

- Score `0.0` → the LLM call failed (check the `error` field / provider key)
- Empty `dimension_scores` on a full transcript
- `alert_level: intervention` on `01` or `07` (excellent calls)
- `competitors_mentioned` empty on `04` (should catch Highspot + Seismic)
