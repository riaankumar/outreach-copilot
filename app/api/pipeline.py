"""Pipeline endpoints — the SDR-facing 'run the AI on this district' button.

Each step is idempotent so the pipeline can be re-run safely. Errors at any
stage are returned in the response payload rather than raising — keeps the
UI honest about partial state.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import District, EmailDraft, Signal, get_session
from app.services import email_drafter, enrichment
from app.services.signal_matcher import resolve_all_unresolved

router = APIRouter()


class SignalOut(BaseModel):
    signal_id: str
    type: str
    date: Optional[str] = None
    match_strategy: Optional[str] = None
    match_confidence: Optional[float] = None
    match_notes: Optional[str] = None
    payload: dict


class CitationLite(BaseModel):
    field_name: str
    source_type: str
    source_url: Optional[str] = None
    source_signal_external_id: Optional[str] = None
    source_quote: Optional[str] = None
    confidence: Optional[float] = None


class PipelineRunResult(BaseModel):
    district_id: str
    status: str
    steps: List[str]
    errors: List[str]


@router.post("/districts/{external_id}/enrich", tags=["pipeline"])
def run_enrich(external_id: str, db: Session = Depends(get_session)) -> dict:
    try:
        e = enrichment.enrich_district(external_id, db)
    except enrichment.EnrichmentError as ex:
        raise HTTPException(400, detail=str(ex))
    return {"district_id": external_id, "enrichment_id": e.id, "fit_score": e.fit_score}


@router.post("/districts/{external_id}/draft", tags=["pipeline"])
def run_draft(external_id: str, db: Session = Depends(get_session)) -> dict:
    try:
        d = email_drafter.draft_email(external_id, db)
    except email_drafter.EmailDraftError as ex:
        raise HTTPException(400, detail=str(ex))
    return {"district_id": external_id, "email_draft_id": d.id, "subject": d.subject}


@router.post("/districts/{external_id}/run-pipeline", response_model=PipelineRunResult, tags=["pipeline"])
def run_pipeline(external_id: str, db: Session = Depends(get_session)) -> PipelineRunResult:
    """End-to-end: re-resolve unresolved signals, enrich, draft email.

    Each step's success is recorded; failures don't abort later steps that
    could still succeed independently.
    """
    d = db.query(District).filter(District.external_id == external_id).one_or_none()
    if d is None:
        raise HTTPException(404, detail=f"District {external_id} not found")
    if d.status in ("duplicate", "non_fit", "rejected"):
        raise HTTPException(400, detail=f"District {external_id} is {d.status}; pipeline not applicable")

    steps: List[str] = []
    errors: List[str] = []

    # Step 1 — resolve any straggling signals
    resolved = resolve_all_unresolved(db)
    steps.append(f"resolve: {resolved}")

    # Step 2 — enrichment
    try:
        e = enrichment.enrich_district(external_id, db)
        steps.append(f"enriched: fit_score={e.fit_score}")
    except enrichment.EnrichmentError as ex:
        errors.append(f"enrichment: {ex}")
        # Without enrichment we can't draft, so bail here
        return PipelineRunResult(district_id=external_id, status=d.status, steps=steps, errors=errors)

    # Step 3 — email draft
    try:
        drafted = email_drafter.draft_email(external_id, db)
        steps.append(f"drafted: subject='{drafted.subject}'")
    except email_drafter.EmailDraftError as ex:
        errors.append(f"draft: {ex}")

    db.refresh(d)
    return PipelineRunResult(district_id=external_id, status=d.status, steps=steps, errors=errors)


@router.get("/districts/{external_id}/signals", response_model=List[SignalOut], tags=["pipeline"])
def list_district_signals(external_id: str, db: Session = Depends(get_session)) -> List[SignalOut]:
    rows = (
        db.query(Signal)
        .filter(Signal.resolved_district_external_id == external_id)
        .order_by(Signal.signal_date.desc().nulls_last())
        .all()
    )
    return [
        SignalOut(
            signal_id=s.external_id,
            type=s.signal_type,
            date=s.signal_date,
            match_strategy=s.match_strategy,
            match_confidence=s.match_confidence,
            match_notes=s.match_notes,
            payload=s.payload or {},
        )
        for s in rows
    ]


@router.get("/districts/{external_id}/citations", response_model=List[CitationLite], tags=["pipeline"])
def list_district_citations(external_id: str, db: Session = Depends(get_session)) -> List[CitationLite]:
    d = db.query(District).filter(District.external_id == external_id).one_or_none()
    if d is None:
        raise HTTPException(404, detail=f"District {external_id} not found")

    cites = []
    if d.enrichment is not None:
        cites.extend(d.enrichment.citations or [])
    if d.email_draft is not None:
        cites.extend(d.email_draft.citations or [])

    return [
        CitationLite(
            field_name=c.field_name,
            source_type=c.source_type,
            source_url=c.source_url,
            source_signal_external_id=c.source_signal_external_id,
            source_quote=c.source_quote,
            confidence=c.confidence,
        )
        for c in cites
    ]


class DraftAction(BaseModel):
    action: str  # "approve" | "edit" | "reject"
    edited_subject: Optional[str] = None
    edited_body: Optional[str] = None
    rejection_reason: Optional[str] = None


@router.patch("/email-drafts/{draft_id}", tags=["pipeline"])
def update_draft(draft_id: int, action: DraftAction, db: Session = Depends(get_session)) -> dict:
    draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).one_or_none()
    if draft is None:
        raise HTTPException(404, detail=f"EmailDraft {draft_id} not found")

    if action.action == "approve":
        draft.status = "approved"
    elif action.action == "edit":
        if action.edited_subject is None and action.edited_body is None:
            raise HTTPException(400, detail="edit requires edited_subject or edited_body")
        draft.status = "edited"
        if action.edited_subject is not None:
            draft.edited_subject = action.edited_subject
        if action.edited_body is not None:
            draft.edited_body = action.edited_body
    elif action.action == "reject":
        draft.status = "rejected"
        draft.rejection_reason = action.rejection_reason
    else:
        raise HTTPException(400, detail=f"unknown action '{action.action}'")

    draft.decided_at = datetime.utcnow()

    # Propagate to district status so the dashboard reflects SDR decisions.
    if draft.status == "approved":
        draft.district.status = "approved"
    elif draft.status == "rejected":
        draft.district.status = "rejected"
    db.commit()
    return {"draft_id": draft_id, "status": draft.status, "district_status": draft.district.status}
