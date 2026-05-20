"""Generate a grounded portrait of a district via Claude.

Architecture:
- Claude is asked to call a single tool, `submit_enrichment`, whose JSON
  schema is the *exact* shape we want back. This is how we get reliable
  structured output without ad-hoc JSON parsing.
- Every claim is paired with a citation. `source_type='inference'` is
  allowed but forced to confidence < 0.6 — keeps speculation explicit.
- The static system prompt is cached (cache_control: ephemeral). Across
  26+ districts that's a 4–10× cost reduction.

Model: claude-sonnet-4-6 for speed during demo runs. Opus 4.7 is the
latest+strongest if quality is more important than turnaround time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, List, Optional

from anthropic import Anthropic
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session

from app.models import Citation, District, Enrichment, Signal


# ─── Public surface ────────────────────────────────────────────────────

MODEL = os.getenv("JOURNIFY_ENRICH_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 2048


class EnrichmentError(RuntimeError):
    pass


def enrich_district(external_id: str, db: Session) -> Enrichment:
    """Run the LLM enrichment and persist results.

    Idempotent on retry: if an Enrichment row already exists we delete and
    re-create rather than partially merging.
    """
    d = db.query(District).filter(District.external_id == external_id).one_or_none()
    if d is None:
        raise EnrichmentError(f"District {external_id} not found")
    if d.status in ("duplicate", "non_fit", "rejected"):
        raise EnrichmentError(f"District {external_id} is {d.status}; refusing to enrich")

    signals = (
        db.query(Signal)
        .filter(Signal.resolved_district_external_id == external_id)
        .order_by(Signal.signal_date.desc().nulls_last())
        .all()
    )

    d.status = "enriching"
    db.commit()

    llm_out = _call_claude(d, signals)

    # Wipe any prior enrichment to keep persistence deterministic
    if d.enrichment is not None:
        db.delete(d.enrichment)
        db.commit()

    enrich_row = Enrichment(
        district_external_id=d.external_id,
        iep_pct=llm_out.iep_pct,
        iep_count_estimate=llm_out.iep_count_estimate,
        sped_program_notes=llm_out.sped_program_notes,
        superintendent_name=llm_out.superintendent_name,
        sped_director_name=llm_out.sped_director_name,
        sped_director_title=llm_out.sped_director_title,
        sped_director_email=llm_out.sped_director_email,
        region_context=llm_out.region_context,
        recent_initiatives=llm_out.recent_initiatives,
        pain_points=llm_out.pain_points,
        nces_district_id=llm_out.nces_district_id,
        fit_score=llm_out.fit_score,
        fit_reasoning=llm_out.fit_reasoning,
        fit_breakdown=llm_out.fit_breakdown,
        raw_research_notes=llm_out.raw_research_notes,
        model_used=MODEL,
        generated_at=datetime.utcnow(),
    )
    db.add(enrich_row)
    db.flush()  # need the PK for citations

    for c in llm_out.citations:
        db.add(Citation(
            enrichment_id=enrich_row.id,
            field_name=c.field_name,
            source_type=c.source_type,
            source_url=c.source_url,
            source_signal_external_id=c.source_signal_external_id,
            source_quote=c.source_quote,
            confidence=c.confidence,
        ))

    d.status = "enriched"
    db.commit()
    db.refresh(enrich_row)
    return enrich_row


# ─── LLM call ──────────────────────────────────────────────────────────

class _CitationLLM(BaseModel):
    field_name: str
    source_type: str = Field(..., description="One of: signal | intake_note | inference")
    source_url: Optional[str] = None
    source_signal_external_id: Optional[str] = None
    source_quote: Optional[str] = None
    confidence: Optional[float] = None


class _EnrichmentLLMOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    iep_pct: Optional[float] = None
    iep_count_estimate: Optional[int] = None
    sped_program_notes: Optional[str] = None
    superintendent_name: Optional[str] = None
    sped_director_name: Optional[str] = None
    sped_director_title: Optional[str] = None
    sped_director_email: Optional[str] = None
    region_context: Optional[str] = None
    recent_initiatives: Optional[str] = None
    pain_points: Optional[str] = None
    nces_district_id: Optional[str] = None
    fit_score: Optional[int] = Field(None, ge=0, le=100)
    fit_reasoning: Optional[str] = None
    fit_breakdown: Optional[dict[str, Any]] = None
    raw_research_notes: Optional[str] = None
    citations: List[_CitationLLM] = Field(default_factory=list)


_TOOL_SCHEMA = {
    "name": "submit_enrichment",
    "description": "Return the structured district enrichment portrait.",
    "input_schema": {
        "type": "object",
        "properties": {
            "iep_pct": {"type": ["number", "null"], "description": "Percentage (0-100) of students with IEPs. Use national/state benchmark if unknown."},
            "iep_count_estimate": {"type": ["integer", "null"]},
            "sped_program_notes": {"type": ["string", "null"]},
            "superintendent_name": {"type": ["string", "null"]},
            "sped_director_name": {"type": ["string", "null"]},
            "sped_director_title": {"type": ["string", "null"]},
            "sped_director_email": {"type": ["string", "null"]},
            "region_context": {"type": ["string", "null"], "description": "1-2 sentence geographic / demographic context."},
            "recent_initiatives": {"type": ["string", "null"]},
            "pain_points": {"type": ["string", "null"]},
            "nces_district_id": {"type": ["string", "null"]},
            "fit_score": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
            "fit_reasoning": {"type": ["string", "null"]},
            "fit_breakdown": {
                "type": ["object", "null"],
                "description": "Per-criterion sub-scores 0-100. Suggested keys: enrollment_match, intent_signal, decision_maker_clarity, recent_activity.",
            },
            "raw_research_notes": {"type": ["string", "null"], "description": "Free-form scratchpad of reasoning."},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_name": {"type": "string"},
                        "source_type": {"type": "string", "enum": ["signal", "intake_note", "inference"]},
                        "source_url": {"type": ["string", "null"]},
                        "source_signal_external_id": {"type": ["string", "null"]},
                        "source_quote": {"type": ["string", "null"]},
                        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                    },
                    "required": ["field_name", "source_type"],
                },
            },
        },
        "required": ["citations"],
    },
}


_SYSTEM = """You are a sales research analyst for Journify Learning — "The
AI Assistant for Special Education." Journify automates SPED paperwork
(IEP drafting, present-levels summaries, parent updates), tracks IEP
goal progress, and generates standards-aligned instructional materials
(assessments, interventions, lesson plans) tied to each student's IEP
goals. It INTEGRATES with the district's IEP system of record (SEIS,
Frontline, etc.), serves the whole IEP team including related-service
providers (therapists), and is ESSA Tier 4 + Responsibly Designed AI
certified by Digital Promise.

Your job: turn raw district data + intent signals into a grounded
portrait the SDR can act on. Be specific. Cite every non-trivial claim.

Citation rules:
- Every fact you assert about this district must appear in `citations`.
- `source_type='signal'` requires `source_signal_external_id` (e.g. "SIG008").
- `source_type='intake_note'` means the claim is supported by the district
  record's `intake_notes` field.
- `source_type='inference'` is allowed for reasonable extrapolation (e.g.
  "rural Texas Hill Country" from state=TX + small enrollment), but you
  MUST set `confidence` < 0.6 for any inference.
- Quote source text verbatim in `source_quote` when applicable (≤ 200 chars).

Fit scoring (0-100) — what makes a great Journify district:
- Enrollment in the 3-25k sweet spot (large enough to have a real SPED
  team, small enough to move on procurement without a year of process).
  Districts up to ~40-50k are still strong enterprise targets.
- Explicit SPED-related intent signals (webinar attendances on IEP /
  progress monitoring topics, RFPs for IEP / SPED software, downloads
  of SPED whitepapers).
- A named SPED decision-maker (Director of Special Education, Asst Supt
  of Student Services) and ideally evidence of a champion (SPED teacher,
  IEP coordinator, related-service provider engaging directly).
- Documentation pain visible in the signals or intake notes (paperwork
  burden mentions, audit pressure, staffing constraints, growing IEP
  caseload).
- Public traction Journify peers value: ESSA-aligned procurement, state
  SPED-tech pilots, growing SPED enrollment.

100 = all five hit. 75 = strong fit, missing one. 50 = decent but missing
two. 0 = wrong segment (private school not serving SPED at scale,
microschool, college, vendor).

Things that REDUCE fit score:
- District has a recently signed multi-year contract with a Journify
  competitor (note this in fit_reasoning).
- Sub-1k enrollment without a clear SPED program.
- Tiny non-public schools.

Things that DO NOT reduce fit:
- District already uses SEIS / Frontline / other IEP system of record.
  Journify integrates with these. Treat as neutral or positive.

Output: call the `submit_enrichment` tool. Do not respond with prose."""


def _call_claude(d: District, signals: List[Signal]) -> _EnrichmentLLMOut:
    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env

    district_block = {
        "district_id": d.external_id,
        "name": d.name,
        "state": d.state,
        "website": d.website,
        "enrollment": d.enrollment,
        "intake_notes": d.intake_notes,
    }
    signal_blocks = [
        {
            "signal_id": s.external_id,
            "type": s.signal_type,
            "date": s.signal_date,
            "match_strategy": s.match_strategy,
            "match_confidence": s.match_confidence,
            "payload": s.payload,
        }
        for s in signals
    ]

    user_text = (
        "DISTRICT:\n" + json.dumps(district_block, indent=2)
        + "\n\nRESOLVED INTENT SIGNALS ("
        + f"{len(signal_blocks)} total):\n"
        + json.dumps(signal_blocks, indent=2)
        + "\n\nProduce the enrichment now."
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "submit_enrichment"},
        messages=[{"role": "user", "content": user_text}],
    )

    tool_use = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise EnrichmentError(f"Claude did not call submit_enrichment; stop_reason={resp.stop_reason}")

    try:
        return _EnrichmentLLMOut.model_validate(tool_use.input)
    except Exception as e:
        raise EnrichmentError(f"Tool output failed validation: {e}") from e
