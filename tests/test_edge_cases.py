"""Edge-case sweep — duplicates + non-fit gating."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models import District
from app.services.edge_cases import sweep
from app.services.normalize import normalize_district_name


def _add(db, ext_id, name, *, state=None, enrollment=None, ingested_at=None):
    d = District(
        external_id=ext_id,
        name=name,
        state=state,
        enrollment=enrollment,
        name_normalized=normalize_district_name(name),
        status="pending",
        ingested_at=ingested_at or datetime.utcnow(),
    )
    db.add(d)
    db.commit()
    return d


def test_duplicate_keeps_older_record(db):
    older = _add(db, "D009", "Brookhaven Public Schools", state="MA", enrollment=6700,
                 ingested_at=datetime(2026, 4, 1))
    newer = _add(db, "D010", "Brookhaven Public Sch.", state="MA", enrollment=6800,
                 ingested_at=datetime(2026, 4, 3))

    report = sweep(db)
    assert report.duplicates_marked == 1

    db.refresh(older)
    db.refresh(newer)
    assert older.status == "pending"  # keeper unchanged
    assert newer.status == "duplicate"
    assert newer.duplicate_of_external_id == "D009"


def test_duplicate_requires_same_state(db):
    # Same normalized name but different states → not a duplicate
    _add(db, "D001", "Larkspur USD", state="CA", ingested_at=datetime(2026, 4, 1))
    _add(db, "D002", "Larkspur USD", state="NY", ingested_at=datetime(2026, 4, 2))
    report = sweep(db)
    assert report.duplicates_marked == 0


def test_non_fit_academy_with_small_enrollment(db):
    d = _add(db, "D006", "Pinecrest Academy", state="FL", enrollment=320)
    sweep(db)
    db.refresh(d)
    assert d.status == "non_fit"
    assert "academy" in d.non_fit_reason.lower()


def test_non_fit_charter_collective(db):
    d = _add(db, "D007", "St. Augustine Charter Collective", state="FL", enrollment=12)
    sweep(db)
    db.refresh(d)
    assert d.status == "non_fit"
    # enrollment 12 is also < 100, either reason is acceptable
    assert d.non_fit_reason is not None


def test_non_fit_tiny_enrollment_without_name_hint(db):
    # No name hint, but enrollment 50 is below the viability floor
    d = _add(db, "D050", "Tiny Hollow School District", state="MT", enrollment=50)
    sweep(db)
    db.refresh(d)
    assert d.status == "non_fit"
    assert "50" in d.non_fit_reason


def test_normal_district_stays_pending(db):
    d = _add(db, "D001", "Larkspur Unified School District", state="CA", enrollment=18500)
    sweep(db)
    db.refresh(d)
    assert d.status == "pending"
    assert d.non_fit_reason is None


def test_sweep_is_idempotent(db):
    _add(db, "D009", "Brookhaven Public Schools", state="MA",
         ingested_at=datetime(2026, 4, 1))
    _add(db, "D010", "Brookhaven Public Sch.", state="MA",
         ingested_at=datetime(2026, 4, 3))
    _add(db, "D006", "Pinecrest Academy", state="FL", enrollment=320)

    first = sweep(db)
    second = sweep(db)
    assert first.duplicates_marked == 1
    assert first.non_fit_marked == 1
    # Second pass converges — nothing new to mark
    assert second.duplicates_marked == 0
    assert second.non_fit_marked == 0


def test_does_not_touch_already_archived(db):
    d = _add(db, "D001", "Larkspur USD", state="CA", enrollment=18500)
    d.status = "rejected"
    db.commit()

    sweep(db)
    db.refresh(d)
    assert d.status == "rejected"  # untouched
