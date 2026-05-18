"""Read endpoints for districts — the UI's main data source."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import District, DistrictOut, EnrichmentOut, EmailDraftOut, get_session

router = APIRouter()


def _district_to_out(d: District) -> DistrictOut:
    enrichment_out: Optional[EnrichmentOut] = None
    if d.enrichment is not None:
        enrichment_out = EnrichmentOut.model_validate(d.enrichment, from_attributes=True)

    email_out: Optional[EmailDraftOut] = None
    if d.email_draft is not None:
        email_out = EmailDraftOut.model_validate(d.email_draft, from_attributes=True)

    return DistrictOut(
        district_id=d.external_id,
        name=d.name,
        state=d.state,
        website=d.website,
        enrollment=d.enrollment,
        intake_notes=d.intake_notes,
        status=d.status,
        duplicate_of_external_id=d.duplicate_of_external_id,
        non_fit_reason=d.non_fit_reason,
        signal_count=len(d.signals or []),
        enrichment=enrichment_out,
        email_draft=email_out,
    )


@router.get("/districts", response_model=List[DistrictOut], tags=["districts"])
def list_districts(db: Session = Depends(get_session)) -> List[DistrictOut]:
    rows = db.query(District).order_by(District.ingested_at.desc()).all()
    return [_district_to_out(d) for d in rows]


@router.get("/districts/{external_id}", response_model=DistrictOut, tags=["districts"])
def get_district(external_id: str, db: Session = Depends(get_session)) -> DistrictOut:
    d = db.query(District).filter(District.external_id == external_id).one_or_none()
    if d is None:
        raise HTTPException(404, detail=f"District {external_id} not found")
    return _district_to_out(d)
