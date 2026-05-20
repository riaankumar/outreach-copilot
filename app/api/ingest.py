"""Ingestion endpoints — the upload boundary.

Design choices:
- Both bulk and single endpoints for each resource. The spec calls out
  "single-record vs bulk" as one of the validation-strategy questions.
- Upsert semantics on `external_id`. This means re-POSTing the same file
  is idempotent — you can re-run the demo without dropping the DB.
- Best-effort batch ingest: one bad record doesn't reject the whole
  batch. The response lists what was skipped and why. This matches how
  real marketing-list ingest behaves.
- Bulk endpoint accepts both the wrapper shape (`{target_districts: [...]}`)
  *and* a bare list. Provided file uses the wrapper; production webhooks
  often send bare arrays.
"""
from __future__ import annotations

from typing import Any, List, Union

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import (
    District,
    DistrictIn,
    IngestAck,
    Signal,
    SignalIn,
    get_session,
)
from app.services.edge_cases import sweep as sweep_edge_cases
from app.services.normalize import normalize_district_name
from app.services.signal_matcher import resolve_all_unresolved, resolve_signal

router = APIRouter()


# ─── Districts ─────────────────────────────────────────────────────────

@router.post("/districts/bulk", response_model=IngestAck, tags=["ingest"])
def ingest_districts_bulk(
    payload: Union[dict, list] = Body(...),
    db: Session = Depends(get_session),
) -> IngestAck:
    """Accepts either {"target_districts": [...]} or a bare list of records.

    Validates each record independently; skipped records are reported but
    don't fail the whole batch.
    """
    records = _unwrap_district_payload(payload)
    return _upsert_districts(records, db)


@router.post("/districts", response_model=IngestAck, tags=["ingest"])
def ingest_district_one(
    record: DistrictIn,
    db: Session = Depends(get_session),
) -> IngestAck:
    """Single-record ingest. Fully validated — no best-effort here."""
    return _upsert_districts([record.model_dump()], db)


# ─── Signals ───────────────────────────────────────────────────────────

@router.post("/signals/bulk", response_model=IngestAck, tags=["ingest"])
def ingest_signals_bulk(
    payload: Union[dict, list] = Body(...),
    db: Session = Depends(get_session),
) -> IngestAck:
    records = _unwrap_signal_payload(payload)
    return _upsert_signals(records, db)


@router.post("/signals", response_model=IngestAck, tags=["ingest"])
def ingest_signal_one(
    record: SignalIn,
    db: Session = Depends(get_session),
) -> IngestAck:
    return _upsert_signals([record.model_dump()], db)


# ─── Helpers ───────────────────────────────────────────────────────────

def _unwrap_district_payload(payload: Union[dict, list]) -> List[dict]:
    if isinstance(payload, dict):
        if "target_districts" in payload:
            return payload["target_districts"]
        # accept a single-record dict
        return [payload]
    if isinstance(payload, list):
        return payload
    raise HTTPException(400, detail="Body must be a list or an object with 'target_districts'")


def _unwrap_signal_payload(payload: Union[dict, list]) -> List[dict]:
    if isinstance(payload, dict):
        if "signals" in payload:
            return payload["signals"]
        return [payload]
    if isinstance(payload, list):
        return payload
    raise HTTPException(400, detail="Body must be a list or an object with 'signals'")


def _upsert_districts(raw_records: List[Any], db: Session) -> IngestAck:
    accepted = 0
    skipped: List[str] = []

    for raw in raw_records:
        try:
            validated = DistrictIn.model_validate(raw)
        except ValidationError as e:
            ext = (raw or {}).get("district_id") if isinstance(raw, dict) else None
            skipped.append(f"{ext or '<no id>'}: {_summarize_validation_error(e)}")
            continue

        existing = (
            db.query(District)
            .filter(District.external_id == validated.district_id)
            .one_or_none()
        )

        if existing is None:
            row = District(
                external_id=validated.district_id,
                name=validated.name,
                state=validated.state,
                website=validated.website,
                enrollment=validated.enrollment,
                intake_notes=validated.intake_notes,
                name_normalized=normalize_district_name(validated.name),
                status="pending",
            )
            db.add(row)
        else:
            existing.name = validated.name
            existing.state = validated.state
            existing.website = validated.website
            existing.enrollment = validated.enrollment
            existing.intake_notes = validated.intake_notes
            existing.name_normalized = normalize_district_name(validated.name)
            # Don't clobber lifecycle status on re-ingest.

        accepted += 1

    db.commit()
    # After districts change, edge cases may need re-evaluation and previously
    # unmatched signals may now resolve.
    sweep_edge_cases(db)
    resolve_all_unresolved(db)
    return IngestAck(accepted=accepted, skipped=len(skipped), skipped_reasons=skipped)


def _upsert_signals(raw_records: List[Any], db: Session) -> IngestAck:
    accepted = 0
    skipped: List[str] = []

    for raw in raw_records:
        try:
            validated = SignalIn.model_validate(raw)
        except ValidationError as e:
            ext = (raw or {}).get("signal_id") if isinstance(raw, dict) else None
            skipped.append(f"{ext or '<no id>'}: {_summarize_validation_error(e)}")
            continue

        # Preserve the full raw payload — signal shapes vary by type and
        # we don't want to lose information at the ingest boundary.
        full_payload = raw if isinstance(raw, dict) else validated.model_dump()

        existing = (
            db.query(Signal)
            .filter(Signal.external_id == validated.signal_id)
            .one_or_none()
        )

        if existing is None:
            row = Signal(
                external_id=validated.signal_id,
                signal_type=validated.type,
                signal_date=validated.date,
                payload=full_payload,
            )
            db.add(row)
        else:
            existing.signal_type = validated.type
            existing.signal_date = validated.date
            existing.payload = full_payload
            # Reset resolution so a re-ingest re-matches.
            existing.resolved_district_external_id = None
            existing.match_confidence = None
            existing.match_strategy = None
            existing.match_notes = None

        accepted += 1

    db.commit()
    # Auto-resolve right after ingest so the UI sees signal_count immediately.
    resolve_all_unresolved(db)
    return IngestAck(accepted=accepted, skipped=len(skipped), skipped_reasons=skipped)


@router.post("/signals/resolve", tags=["signals"])
def resolve_signals(db: Session = Depends(get_session)) -> dict:
    """Re-run matching on every currently-unresolved signal. Idempotent."""
    return resolve_all_unresolved(db)


@router.get("/signals/unmatched", tags=["signals"])
def list_unmatched_signals(db: Session = Depends(get_session)) -> List[dict]:
    """Signals the matcher couldn't confidently resolve to a district.

    SDRs use this to find signals worth manually triaging — generic email
    domains, IP-only content views, names that didn't fuzzy-match well.
    Each row carries `match_notes` explaining *why* it didn't resolve, so
    the SDR has the evidence trail.
    """
    rows = (
        db.query(Signal)
        .filter(Signal.resolved_district_external_id.is_(None))
        .order_by(Signal.signal_date.desc().nulls_last())
        .all()
    )
    return [
        {
            "signal_id": s.external_id,
            "type": s.signal_type,
            "date": s.signal_date,
            "match_notes": s.match_notes,
            "payload": s.payload,
        }
        for s in rows
    ]


def _summarize_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", []))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts) if parts else "validation failed"
