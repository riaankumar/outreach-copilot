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
        body=clean_body(llm_out.composed_body()),
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
    # The LLM returns three required paragraph parts. The server composes
    # them into `body` with blank lines between, guaranteeing structure.
    body_hook: Optional[str] = None
    body_introduction: Optional[str] = None
    body_cta: Optional[str] = None
    # Legacy/fallback: a pre-composed body. Tests can still set this directly.
    body: Optional[str] = None
    hook_summary: Optional[str] = None
    citations: List[_CitationLLM] = Field(default_factory=list)

    def composed_body(self) -> str:
        """Compose body from the three parts, falling back to a pre-set body."""
        if self.body_hook and self.body_introduction and self.body_cta:
            return f"{self.body_hook.strip()}\n\n{self.body_introduction.strip()}\n\n{self.body_cta.strip()}"
        return (self.body or "").strip()


_TOOL_SCHEMA = {
    "name": "submit_draft",
    "description": "Return the structured outreach email draft.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recipient_name": {"type": ["string", "null"]},
            "recipient_title": {"type": ["string", "null"]},
            "recipient_email": {"type": ["string", "null"]},
            "subject": {
                "type": "string",
                "description": (
                    "Structure: '[district-specific hook or outcome], by Journify'. "
                    "The HOOK leads (a specific district anchor + outcome or timing "
                    "promise), 'by Journify' or '| Journify' closes. 5-9 words total. "
                    "The reader gets the value first, the brand second. "
                    "Good: 'Grandview's 1,000 IEPs cut in half, by Journify', "
                    "'RFP-2026-014 scoped in time, by Journify', "
                    "'James, 4 hrs back per day, by Journify', "
                    "'Brookhaven's audit-ready in time, by Journify', "
                    "'Salt Lake's RFP, scoped in two weeks | Journify'. "
                    "Bad: 'Scoped to your RFP, in time' (no Journify, no district), "
                    "'Journify for Grandview' (Journify leads instead of hook), "
                    "'4 hours back per day' (no district anchor), "
                    "'RFP-XXX, on time' (banned previous default)."
                ),
            },
            "body_hook": {
                "type": "string",
                "description": "PARAGRAPH 1. 1-2 sentences. Names the specific signal with a date or detail. Must be unmistakably about this district.",
            },
            "body_introduction": {
                "type": "string",
                "description": "PARAGRAPH 2. 2-3 sentences. Connects the signal to operational pressure (use enrichment numbers when available) then states what changes for the SPED team or kids. Outcomes, not features. May quote Journify's real claims (4+ hours back per day, IEP materials in <5 min, ESSA Tier 4) but never fabricate stats.",
            },
            "body_cta": {
                "type": "string",
                "description": "PARAGRAPH 3. 1 sentence. ONE ask, ONE time anchor. No two-option asks.",
            },
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
        "required": ["subject", "body_hook", "body_introduction", "body_cta", "citations"],
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

═══ MISSION / PURPOSE (use this lens whenever it fits) ═══

Journify exists because SPED teachers spend more than half their time on
paperwork instead of with students. Tie outcomes back to that mission,
but VARY the phrasing — never use the same wording across two emails.
A menu to pick from (or write a fresh variant in this spirit):
  "...so case managers get back to teaching kids."
  "...so therapists run sessions instead of writing them up."
  "...so progress monitoring isn't a Sunday-night job."
  "...so your team's afternoons go to students, not screens."
  "...so the people who chose this field actually get to do it."
  "...so paperwork stops eating instruction time."

═══ ON-TIME DELIVERY (use when a deadline is in the signals) ═══

If the district has a procurement deadline in the signals (RFP due date,
fiscal-year window, board vote), name it and commit to it. Pick a fresh
phrasing each time — do not template:
  "Proposals close [date]. We can have a scoped response in front of
   your committee well before then."
  "Two weeks from award, we can have a working pilot configured to your
   IEP system."
  "If your board wants something for the [next meeting], we can scope a
   demo to that date."
  "We'll have a tailored response ready in time to make your shortlist."
Never invent a deadline. If signals don't carry one, skip this beat.

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
Then say what specifically changes for the SPED team or for kids, tied
back to Journify's mission. If there's a deadline in the signals,
commit to delivering on it.

YOU MUST VARY which Journify claim you lead with. Pick ONE per email,
ideally the one that maps best to the specific signal:
  - "Teachers report saving more than four hours per day."
    Lean on this when paperwork load is the visible pain.
  - "Up to 50% time savings on paperwork."
    Lean on this when the signal is about efficiency / staffing.
  - "IEP materials generated in less than five minutes."
    Lean on this when the signal is about quality or speed.
  - "Educators rate the quality of instructional supports 9.5 / 10."
    Lean on this when the signal is about teacher buy-in or evaluations.
  - "10,000+ students across 17 states."
    Lean on this for districts that want social proof / scale.
  - "ESSA Tier 4 research certified through Digital Promise."
    Lean on this when the signal is about evidence-based procurement,
    grant funding, or a research-minded buyer.
Do NOT quote more than one of these in the same email. Pick the most
relevant. The rest of the value paragraph should be the unique-to-this-
district connection, not stat-stacking.

If you don't quote a specific claim, go directional ("tends to", "most
teams", "usually"). Never invent a percentage that isn't on the list above.

Vary opening style across districts. Not every email starts with
"At ~N IEPs...". Try alternatives:
  - Lead with the pain: "Compliance paperwork at [N] students is its
    own staffing problem."
  - Lead with the buyer's job: "Most SPED directors hit this wall
    around [N]."
  - Lead with the team: "Your case managers and therapists are doing
    the same documentation twice without a shared system."

Hard fail: "Our AI-powered platform automates compliance workflows
seamlessly." (Don't ever).

PARAGRAPH 3 — CTA (1 sentence)
ONE ask. ONE time anchor. VARY the phrasing across emails — do not
default to "Worth X minutes" every time. A menu to pick from (or write
a fresh variant in this spirit):
  - "Worth 20 minutes Tuesday or Wednesday?"
  - "Open to a 15-minute walk-through this Thursday?"
  - "Want me to send the one-page brief we use with districts your size?"
  - "Should I get on your calendar before the May 30 deadline?"
  - "If a scoped pilot proposal is useful, I can have one to you by
     [date]."
  - "Happy to send the case study from [comparable district] if that's
     more useful than a call right now."
  - "Quick reply if you want me to scope a response to your RFP, or a
     30-minute conversation if you'd rather start there?"  (ONE ask
     framed as two micro-choices is OK; two separate asks is not)

Bad: "Would a 15-minute call this week or next be useful? I'm also
      happy to send a one-pager first if that's easier." (two separate
      options + filler)

No signoff name. The SDR adds their own. No P.S.

═══ SUBJECT LINE — hook leads, "by Journify" trails ═══

STRUCTURE: '[district-specific hook + outcome], by Journify'

The HOOK comes first — a specific district anchor combined with an
outcome promise or timing commitment. Then a trailing brand
attribution: ", by Journify" or " | Journify".

Why this order: the recipient skims their inbox and reads the front of
the subject first. Lead with the value (their district, the outcome).
The brand attribution at the tail says who is doing the saying.

Rules:
- 5-9 words total.
- MUST end with ", by Journify" or " | Journify".
- The HOOK (everything before "by Journify") MUST contain:
  - the district name OR a unique district anchor (RFP number,
    recipient first name, named program, specific event), AND
  - an outcome or timing word (half, hours, in time, before [date],
    cuts, saves, two weeks, ready, scoped, back).
- NO em dashes. Colons OK only if they earn it.
- The hook reads like a specific promise to that district. Not a
  generic claim with a name slotted in.

A menu of patterns. Pick the ONE that best fits THIS district's signals.
Substitute the district name, RFP number, recipient name, and outcome
where bracketed. Vary across districts; never use the same template
twice in a row.

When the district has a procurement deadline:
  "RFP-[number] scoped in time, by Journify"
  "[District]'s RFP scoped, by Journify"
  "[District]'s RFP, scoped in two weeks | Journify"
  "Pilot in front of [District] by [date], by Journify"
  "Ready before [District]'s [date] deadline | Journify"

When the district has a strong outcome story (IEP count, paperwork load):
  "[District]'s 1,000 IEPs, half the time, by Journify"
  "[District] saves 4 hrs/day | Journify"
  "[District]'s case managers, freed | Journify"
  "Half the paperwork for [District] | Journify"
  "IEPs in 5 minutes for [District], by Journify"

When the recipient is the strongest anchor:
  "[First name], 4 hrs back per day, by Journify"
  "[First name]'s SPED team, in one place | Journify"
  "[First name], your RFP scoped in time, by Journify"

When the framing is mission / purpose (signals about documentation
pain, no procurement on the table):
  "[District]'s teachers, afternoons back | Journify"
  "Less paperwork, more students for [District] | Journify"
  "[District]'s Sunday nights, back, by Journify"

BAD subjects (do not use):
  "Two downloads of the evidence-base paper"   (no Journify, no district)
  "Following up on your interest"               (generic)
  "Quick question about [District]"             (generic + filler word)
  "AI for special education"                    (generic vendor pitch)
  "RFP-XXXX question"                           (no Journify, filler)
  "RFP-XXXX, on time"                           (no Journify, banned default)
  "Scoped to your RFP, in time"                 (no Journify, no district)
  "Built for your RFP scope"                    (no Journify, no district)
  "4 hours back per day"                        (no Journify, no district)
  "Journify for [District]"                     (Journify leads — hook should lead)
  "Journify cuts [District]'s IEP load"         (Journify leads — hook should lead)
  "Replace your IEP system"                     (we integrate, never replace)

Litmus test: the hook should land before the comma. After the comma, the
brand attribution closes. If the brand leads, rewrite.

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
