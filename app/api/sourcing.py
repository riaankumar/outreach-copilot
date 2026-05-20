"""Sourcing endpoints.

POST /api/sourcing/search  — given a district name or URL, scrape + verify
POST /api/sourcing/add     — commit a sourced packet to the pipeline
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import District, Signal, get_session
from app.services import sourcing
from app.services.normalize import normalize_district_name
from app.services.signal_matcher import resolve_all_unresolved

router = APIRouter()


class SearchReq(BaseModel):
    query: str


@router.post("/sourcing/search", tags=["sourcing"])
def search(req: SearchReq) -> dict:
    """Run the sourcing agent + verification layer. Returns the packet."""
    if not req.query.strip():
        raise HTTPException(400, detail="query is required")
    sourced = sourcing.source_district(req.query.strip())
    sourcing.verify_sourced_district(sourced)
    return sourced.model_dump()


class AddReq(BaseModel):
    sourced: dict  # SourcedDistrict serialized
    external_id: Optional[str] = None  # if omitted, we generate one


@router.post("/sourcing/add", tags=["sourcing"])
def add(req: AddReq, db: Session = Depends(get_session)) -> dict:
    """Commit a sourced packet to the pipeline.

    Creates a District row and an intake-note signal capturing the sourcing
    hook. Idempotent on external_id.
    """
    s = sourcing.SourcedDistrict.model_validate(req.sourced)

    # Generate a sourced external_id if not provided. Format: SRC-<slug>
    ext_id = req.external_id or _make_external_id(s, db)

    existing = db.query(District).filter(District.external_id == ext_id).one_or_none()
    if existing is None:
        d = District(
            external_id=ext_id,
            name=s.name,
            state=s.state,
            website=_clean_website(s.website),
            enrollment=s.enrollment,
            intake_notes=_intake_notes_from_sourced(s),
            name_normalized=normalize_district_name(s.name),
            status="pending",
        )
        db.add(d)
    else:
        # Update existing — refresh fields, keep lifecycle status
        existing.name = s.name
        existing.state = s.state or existing.state
        existing.website = _clean_website(s.website) or existing.website
        existing.enrollment = s.enrollment or existing.enrollment
        existing.intake_notes = _intake_notes_from_sourced(s)
        existing.name_normalized = normalize_district_name(s.name)

    db.commit()

    # Create a synthetic "sourcing_event" signal so the matcher / enrichment
    # pass picks up the hook material and SPED contacts.
    sig_external_id = f"SRC-{ext_id}"
    sig = db.query(Signal).filter(Signal.external_id == sig_external_id).one_or_none()
    payload = {
        "signal_id": sig_external_id,
        "type": "sourcing_event",
        "date": None,
        "district_id": ext_id,
        "source_query": _intake_short(s),
        "email_domain": s.email_domain,
        "email_format": s.email_format,
        "sped_director_name": s.sped_director.name if s.sped_director else None,
        "sped_director_title": s.sped_director.title if s.sped_director else None,
        "sped_director_email": s.sped_director.email if s.sped_director else None,
        "superintendent_name": s.superintendent.name if s.superintendent else None,
        "key_hook": s.key_hook,
        "confidence": s.confidence,
        "flags": s.flags,
    }
    if sig is None:
        db.add(Signal(
            external_id=sig_external_id,
            signal_type="sourcing_event",
            signal_date=None,
            payload=payload,
            resolved_district_external_id=ext_id,
            match_confidence=s.confidence,
            match_strategy="direct_id",
            match_notes="sourced by sourcing agent",
        ))
    else:
        sig.payload = payload
        sig.resolved_district_external_id = ext_id
        sig.match_confidence = s.confidence
    db.commit()

    # Auto-resolve any other signals that now have a target
    resolve_all_unresolved(db)

    return {"district_id": ext_id, "created": existing is None, "confidence": s.confidence}


# ─── Helpers ─────────────────────────────────────────────────

def _make_external_id(s: sourcing.SourcedDistrict, db: Session) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", s.name.lower()).strip("-")[:24]
    base = f"SRC-{slug or 'district'}"
    candidate = base
    i = 1
    while db.query(District).filter(District.external_id == candidate).one_or_none():
        i += 1
        candidate = f"{base}-{i}"
    return candidate


def _clean_website(w: Optional[str]) -> Optional[str]:
    if not w:
        return None
    w = w.strip().lower()
    for p in ("https://", "http://"):
        if w.startswith(p):
            w = w[len(p):]
    return w.rstrip("/")


def _intake_short(s: sourcing.SourcedDistrict) -> str:
    bits = ["Sourced via sourcing agent"]
    if s.city and s.state:
        bits.append(f"{s.city}, {s.state}")
    return " - ".join(bits)


def _intake_notes_from_sourced(s: sourcing.SourcedDistrict) -> str:
    """One-line intake note for the District row, similar to inbound forms."""
    parts = [f"Sourced via web research"]
    if s.key_hook:
        parts.append(s.key_hook.rstrip("."))
    return ". ".join(parts) + "."
