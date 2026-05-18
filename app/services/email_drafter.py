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
MAX_TOKENS = 1500


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
        subject=llm_out.subject,
        body=llm_out.body,
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
            "subject": {"type": "string", "description": "≤ 60 chars, specific, no clickbait."},
            "body": {"type": "string", "description": "120–180 words. Plain text. No subject line, no signoff name (SDR adds their own)."},
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


_SYSTEM = """You are an SDR for Journify writing the first-touch email to
a K-12 district decision-maker about Journify's AI tools for special
education paperwork and IEP compliance.

Guidelines:
- Open with a specific, recent, district-particular hook drawn from the
  resolved signals or intake notes. Never with "I hope this finds you well".
- One concrete claim about Journify, tied to their context.
- One clear, low-friction ask (15-min call, a 1-pager, intro to a peer).
- Plain text. No bullets unless absolutely necessary. No subject in the body.
- 120–180 words.
- Use the recipient's actual name/title if known. Otherwise address by role
  ("Hi Director of Special Education,") and leave recipient_name=null.

Citation rules:
- `field_name="hook"` cites where the hook came from (signal id or
  intake_note).
- `field_name="recipient"` cites where you got the recipient identity.
- Any specific district claim (program name, dollar amount, hire) needs a
  citation tied to its source.
- `source_type='inference'` only when extrapolating beyond the data.

Output: call `submit_draft`. Do not return prose."""


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
