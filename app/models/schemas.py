"""Pydantic schemas — the API's contract with the outside world.

We keep these separate from SQLAlchemy models because:
1. The API shape is allowed to evolve independently of the DB.
2. We never want to accidentally leak internal fields (e.g., raw_research_notes).
3. Validation rules live here, close to the boundary.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, List, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


# ─── Ingestion (what the client POSTs in) ──────────────────────────────

class DistrictIn(BaseModel):
    """One district record as it arrives from marketing / inbound forms.

    Permissive: we accept partial data because the real world is messy.
    Required fields are kept to a minimum.
    """
    district_id: str = Field(..., min_length=1, description="External ID, e.g. 'D001'")
    name: str = Field(..., min_length=2)
    state: Optional[str] = None
    website: Optional[str] = None
    enrollment: Optional[int] = Field(None, ge=0)
    intake_notes: Optional[str] = None

    @field_validator("website")
    @classmethod
    def strip_protocol(cls, v: Optional[str]) -> Optional[str]:
        """Normalize websites: 'https://foo.org/' → 'foo.org'."""
        if not v:
            return v
        v = v.strip().lower()
        for prefix in ("https://", "http://"):
            if v.startswith(prefix):
                v = v[len(prefix):]
        return v.rstrip("/")


class DistrictsBulkIn(BaseModel):
    """Bulk upload wrapper — matches the shape of the provided file."""
    target_districts: List[DistrictIn]


class SignalIn(BaseModel):
    """One intent signal. Shape varies wildly by `type` — we keep the rest
    in a free-form dict so we don't lose information.

    The matcher (services/signal_matcher.py) inspects the payload to figure
    out which district this belongs to.
    """
    model_config = ConfigDict(extra="allow")  # preserve unknown fields in payload

    signal_id: str
    type: str
    date: Optional[str] = None


class SignalsBulkIn(BaseModel):
    signals: List[SignalIn]


# ─── Read models (what the API returns) ────────────────────────────────

class CitationOut(BaseModel):
    field_name: str
    source_type: str
    source_url: Optional[str] = None
    source_signal_external_id: Optional[str] = None
    source_quote: Optional[str] = None
    confidence: Optional[float] = None


class EnrichmentOut(BaseModel):
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
    fit_score: Optional[int] = None
    fit_reasoning: Optional[str] = None
    fit_breakdown: Optional[dict[str, Any]] = None
    citations: List[CitationOut] = []
    generated_at: Optional[datetime] = None


class EmailDraftOut(BaseModel):
    id: Optional[int] = None
    recipient_name: Optional[str] = None
    recipient_title: Optional[str] = None
    recipient_email: Optional[str] = None
    subject: str
    body: str
    hook_summary: Optional[str] = None
    edited_subject: Optional[str] = None
    edited_body: Optional[str] = None
    status: str
    citations: List[CitationOut] = []
    generated_at: Optional[datetime] = None


class DistrictOut(BaseModel):
    district_id: str
    name: str
    state: Optional[str] = None
    website: Optional[str] = None
    enrollment: Optional[int] = None
    intake_notes: Optional[str] = None
    status: str
    duplicate_of_external_id: Optional[str] = None
    non_fit_reason: Optional[str] = None
    signal_count: int = 0
    # Derived for the pipeline view: "send_today" | "this_week" | "later" | None
    send_priority: Optional[str] = None
    enrichment: Optional[EnrichmentOut] = None
    email_draft: Optional[EmailDraftOut] = None


# ─── Acks (what ingest endpoints return) ───────────────────────────────

class IngestAck(BaseModel):
    accepted: int
    skipped: int
    skipped_reasons: List[str] = []
