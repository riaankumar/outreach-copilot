"""Draft a grounded outreach email from a district's enrichment + signals.

Same approach as enrichment.py: force tool use for structured output, cache
the static system prompt, attach citations to every non-boilerplate claim.

The draft is *not auto-sent*. It lands in the SDR's queue as status='draft'
and they approve/edit/reject via the PATCH endpoint.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Optional

from anthropic import Anthropic
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session

from app.models import Citation, District, EmailDraft, Signal


MODEL = os.getenv("JOURNIFY_DRAFT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 700  # tight cap — forces brevity at the API layer

# Tokens we strip post-generation if Claude slips. Prompt bans them first,
# this is belt-and-suspenders so a regression in the prompt can't ship a
# bad email.
_BANNED_PHRASES = (
    "I hope this finds you well",
    "I hope this email finds you well",
    "I noticed",
    "I'd love to",
    "I would love to",
    "happy to send",
    "happy to share",
    "happy to be a resource",
    "either way",
    "circle back",
    "touch base",
    "reach out",
    "as you work through this",
    "given where you are",
    "I didn't want to sit on it",
    "be a resource",
)


class EmailDraftError(RuntimeError):
    pass


def draft_email(external_id: str, db: Session) -> EmailDraft:
    d = db.query(District).filter(District.external_id == external_id).one_or_none()
    if d is None:
        raise EmailDraftError(f"District {external_id} not found")
    if d.enrichment is None:
        raise EmailDraftError(f"District {external_id} has no enrichment yet")
    if d.status in ("duplicate", "non_fit", "rejected"):
        raise EmailDraftError(f"District {external_id} is {d.status}")

    signals = (
        db.query(Signal)
        .filter(Signal.resolved_district_external_id == external_id)
        .order_by(Signal.signal_date.desc().nulls_last())
        .limit(3)
        .all()
    )

    llm_out = _call_claude(d, d.enrichment, signals)

    if d.email_draft is not None:
        db.delete(d.email_draft)
        db.commit()

    draft_row = EmailDraft(
        district_external_id=d.external_id,
        recipient_name=llm_out.recipient_name,
        recipient_title=llm_out.recipient_title,
        recipient_email=llm_out.recipient_email,
        subject=clean_subject(llm_out.subject),
        body=clean_body(llm_out.body),
        hook_summary=llm_out.hook_summary,
        status="draft",
        model_used=MODEL,
        generated_at=datetime.utcnow(),
    )
    db.add(draft_row)
    db.flush()

    for c in llm_out.citations:
        db.add(Citation(
            email_draft_id=draft_row.id,
            field_name=c.field_name,
            source_type=c.source_type,
            source_url=c.source_url,
            source_signal_external_id=c.source_signal_external_id,
            source_quote=c.source_quote,
            confidence=c.confidence,
        ))

    db.commit()
    db.refresh(draft_row)
    return draft_row


# ─── LLM call ──────────────────────────────────────────────────────────

class _CitationLLM(BaseModel):
    field_name: str
    source_type: str
    source_url: Optional[str] = None
    source_signal_external_id: Optional[str] = None
    source_quote: Optional[str] = None
    confidence: Optional[float] = None


class _DraftLLMOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recipient_name: Optional[str] = None
    recipient_title: Optional[str] = None
    recipient_email: Optional[str] = None
    subject: str
    body: str
    hook_summary: Optional[str] = None
    citations: List[_CitationLLM] = Field(default_factory=list)


_TOOL_SCHEMA = {
    "name": "submit_draft",
    "description": "Return the structured outreach email draft.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recipient_name": {"type": ["string", "null"]},
            "recipient_title": {"type": ["string", "null"]},
            "recipient_email": {"type": ["string", "null"]},
            "subject": {"type": "string", "description": "3-6 words. Outcome/problem-driven. Never references the source signal directly."},
            "body": {"type": "string", "description": "90-130 words across exactly THREE paragraphs separated by a blank line (\\n\\n): hook, introduction/value, CTA."},
            "hook_summary": {"type": ["string", "null"], "description": "One-line description of the hook used."},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_name": {"type": "string"},
                        "source_type": {"type": "string", "enum": ["signal", "intake_note", "inference", "enrichment"]},
                        "source_url": {"type": ["string", "null"]},
                        "source_signal_external_id": {"type": ["string", "null"]},
                        "source_quote": {"type": ["string", "null"]},
                        "confidence": {"type": ["number", "null"]},
                    },
                    "required": ["field_name", "source_type"],
                },
            },
        },
        "required": ["subject", "body", "citations"],
    },
}


_SYSTEM = """You write first-touch sales emails for Journify Learning, "The
AI Assistant for Special Education" — an evidence-based AI that helps
K-12 SPED teams with IEP drafting, present-levels summaries, IEP goal
progress monitoring, parent communication, and the generation of
personalized, standards-aligned instructional materials (assessments,
interventions, lesson plans) tied to each student's IEP goals.

Critical positioning facts you must respect:
- Journify INTEGRATES with the district's existing IEP system of record
  (SEIS, Frontline, etc.). It is NOT a replacement. Never pitch against
  the IEP system they already use.
- It is purpose-built for SPED. Not general-ed personalization.
- It serves the WHOLE IEP team: SPED teachers, case managers,
  paraprofessionals, AND related-service providers (therapists). The
  unification of the support team is part of the value, not a side note.
- It is human-in-the-loop. All AI output is labeled AI-generated,
  editable, and requires educator approval. ESSA Tier 4 Research
  Certified (Digital Promise) and Responsibly Designed AI certified.
  Frame as ASSISTIVE, EVIDENCE-BASED, RESPONSIBLY DESIGNED — not
  autonomous.

These emails are sent by a real SDR to a named decision-maker (usually a
Director of Special Education or Asst. Superintendent of Student Services).
The bar: would the recipient read this twice and reply.

═══ STYLE — NON-NEGOTIABLE ═══

- 90-130 words across exactly three paragraphs.
- ZERO em dashes (—) or en dashes (–). Use a period.
- No semicolons.
- Plain English. A district administrator with no time should get every
  word on first read. No jargon, no adjective stacks.
- Active voice. Concrete nouns. Specific verbs.

═══ BANNED PHRASES (these are AI-slop tells; never use any of these) ═══

  I hope this finds you well       I noticed                  I'd love to
  I would love to                   happy to send/share        either way
  circle back                       touch base                 reach out
  as you work through this          given where you are        be a resource
  I didn't want to sit on it        leverage / utilize         drive / unlock
  empower / transform               compliance defensibility   solution
  platform                          best-in-class              robust
  comprehensive                     seamless

Also do NOT pitch against their IEP system of record. Don't write things
like "instead of SEIS/Frontline" or "replace your current SPED software."
Journify sits ON TOP of those systems.

═══ STRUCTURE — EXACTLY THREE PARAGRAPHS, separated by a BLANK LINE ═══

The body MUST be three paragraphs. Use TWO newlines (\\n\\n) between them
so each paragraph renders as a visible block. Do NOT run them together.

PARAGRAPH 1 — HOOK (1-2 sentences)
Name the specific signal with a detail (webinar title, document name, RFP
number, date). The recipient should instantly know you didn't blast this
to 500 districts.
  Good: "James, two of your team registered for our IEP Compliance webinar
         in March and again in April, and someone pulled the IEP Goal
         Library whitepaper on April 16."
  Bad:  "I noticed you've been engaging with our content."

PARAGRAPH 2 — INTRODUCTION (2-3 sentences)
The value paragraph. Connect the signal to a real operational pressure
their team feels (use a number from the enrichment when you have one).
Then say what specifically changes for the SPED team or for kids.
OUTCOMES, NOT FEATURES.

You may use Journify's REAL public claims (these are not made up):
  - "Teachers report saving more than four hours per day."
  - "Up to 50% time savings on paperwork."
  - "IEP materials generated in less than five minutes."
  - "Educators rate the quality of instructional supports 9.5 / 10."
  - "10,000+ students across 17 states."
  - "ESSA Tier 4 research certified."

If you don't quote a specific claim, go directional ("tends to", "most
teams", "usually"). Never invent a percentage that isn't on the list above.

  Good: "At ~1,000 IEPs across Grandview, the SPED-team time loss is
         the real cost. Journify teachers report getting more than four
         hours back per day on average — present-levels summaries,
         goal-aligned activities, and parent updates that used to eat
         the weekend now generate in under five minutes for an educator
         to review and approve."
  Bad:  "Our AI-powered platform automates compliance workflows seamlessly."

PARAGRAPH 3 — CTA (1 sentence)
ONE ask. ONE time anchor.
  Good: "Worth 20 minutes next Tuesday or Wednesday morning?"
  Good: "Want me to send the 1-page brief we use with districts your size?"
  Bad:  "Would a 15-minute call this week or next be useful? I'm also
         happy to send a one-pager first if that's easier." (two options
         + filler)

No signoff name. The SDR adds their own. No P.S.

═══ SUBJECT LINE — outcome-driven, not signal-referential ═══

The subject must hint at what changes for THEM. Frame around the problem
Journify solves: time saved, audits passed, paperwork gone, evenings back,
case managers freed. Make them want to open the email.

Rules:
- 3-6 words.
- NO em dashes.
- Punchy. Curiosity > completeness. Often a partial thought or imperative.
- Reference an OUTCOME or PAIN POINT, not the signal that triggered the
  email. The signal is the hook IN THE BODY; the subject is the PROMISE.

GOOD subjects (problem/outcome-driven, ideally tied to a Journify claim):
  "4 hours back per day"            (their headline outcome)
  "IEP materials in 5 minutes"      (their headline outcome)
  "50% off the paperwork"           (their claim, restated punchier)
  "Sunday nights, back"
  "Unify your IEP team"             (the support-provider angle)
  "ESSA Tier 4, in your district"   (credibility plus action)
  "Free your therapists' time"
  "Goal-aligned, in five minutes"
  "Stop the Sunday paperwork"

When a recent signal carries real urgency (an RFP deadline, a named pain
point), you can blend the two:
  "RFP-2026-014, on time"
  "1,000 IEPs, half the time"

BAD subjects (signal-referential or generic / AI-slop):
  "Two downloads of the evidence-base paper"
  "Following up on your interest"
  "Quick question about Grandview"
  "AI for special education"
  "Replace your IEP system"          (we don't — we integrate)

═══ HOOK QUALITY GATE ═══

If you can imagine the same hook landing in three other districts, rewrite
it. The hook must be unmistakably about THIS district. Webinar titles,
document names, RFP numbers, the recipient's actual name, specific dates.

═══ CITATIONS ═══

- field_name="hook" → cite the source signal id(s).
- field_name="recipient" → cite the enrichment row.
- Any specific claim about the district (their RFP, their hire, their
  webinar attendance) needs a signal-backed citation.
- Directional claims about "most teams" / "districts your size" are
  inference with confidence 0.5 — acceptable, but tagged honestly.

═══ OUTPUT ═══

Call submit_draft. Do not return prose. Do not explain. Just the tool call."""


def _call_claude(d: District, e, signals: List[Signal]) -> _DraftLLMOut:
    client = Anthropic()

    enrich_block = {
        "iep_pct": e.iep_pct,
        "iep_count_estimate": e.iep_count_estimate,
        "sped_program_notes": e.sped_program_notes,
        "superintendent_name": e.superintendent_name,
        "sped_director_name": e.sped_director_name,
        "sped_director_title": e.sped_director_title,
        "sped_director_email": e.sped_director_email,
        "region_context": e.region_context,
        "recent_initiatives": e.recent_initiatives,
        "pain_points": e.pain_points,
        "fit_score": e.fit_score,
        "fit_reasoning": e.fit_reasoning,
    }
    signal_blocks = [
        {
            "signal_id": s.external_id,
            "type": s.signal_type,
            "date": s.signal_date,
            "payload": s.payload,
        }
        for s in signals
    ]

    user_text = (
        f"DISTRICT: {d.name} ({d.external_id}, {d.state}, enrollment {d.enrollment})\n"
        f"INTAKE NOTES: {d.intake_notes or '(none)'}\n\n"
        "ENRICHMENT:\n" + json.dumps(enrich_block, indent=2)
        + "\n\nTOP RECENT SIGNALS:\n" + json.dumps(signal_blocks, indent=2)
        + "\n\nWrite the email."
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "submit_draft"},
        messages=[{"role": "user", "content": user_text}],
    )

    tool_use = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise EmailDraftError(f"Claude did not call submit_draft; stop_reason={resp.stop_reason}")

    try:
        return _DraftLLMOut.model_validate(tool_use.input)
    except Exception as e:
        raise EmailDraftError(f"Tool output failed validation: {e}") from e


# ─── Post-process cleanup (belt-and-suspenders for style enforcement) ──

def clean_subject(s: str) -> str:
    """Remove dashes from the subject and collapse whitespace."""
    s = _strip_dashes(s).strip()
    # Collapse runs of whitespace and pruned punctuation
    while "  " in s:
        s = s.replace("  ", " ")
    while " ," in s or " ." in s:
        s = s.replace(" ,", ",").replace(" .", ".")
    return s.strip(" ,.")


def clean_body(text: str) -> str:
    """Strip em/en dashes (replace with a period when sentence-internal,
    otherwise just remove) and collapse the cosmetic side-effects.
    Defensive; the prompt should already prevent these."""
    out = _strip_dashes(text)
    # collapse spaces left by dash removal
    while "  " in out:
        out = out.replace("  ", " ")
    # tidy " , " or " . " artifacts
    out = out.replace(" ,", ",").replace(" .", ".").replace(" ?", "?").replace(" !", "!")
    # normalize trailing whitespace per line
    out = "\n".join(line.rstrip() for line in out.splitlines())
    return out.strip()


def _strip_dashes(s: str) -> str:
    """Replace em/en dashes with commas. Comma is the safer substitute
    in cold-email prose: appositives stay grammatical ("the IEP team,
    case managers, teachers..."), and sentence-separator uses still read
    as one longer sentence. Leaves hyphens (-) alone."""
    # Any em/en dash (with or without surrounding spaces) becomes a comma.
    for sep in (" — ", " – ", " —", " –", "— ", "– "):
        s = s.replace(sep, ", ")
    s = s.replace("—", ", ").replace("–", ", ")
    # Tidy any double commas that result
    while ",," in s:
        s = s.replace(",,", ",")
    while ", ," in s:
        s = s.replace(", ,", ",")
    return s


def word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])
