"""Sweep districts for lifecycle states the matcher can't determine alone.

- **duplicate**: same normalized name + state. The older row (by ingested_at)
  wins; the newer is marked `status='duplicate'` and `duplicate_of_external_id`
  is set. Catches D009/D010 (Brookhaven Public Schools / Brookhaven Public Sch.).
- **non_fit**: too small to be a public K-12 district. Catches D006 Pinecrest
  Academy (320 students), D007 St. Augustine Charter Collective (12 students).

Idempotent — re-running converges on the same labels.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from sqlalchemy.orm import Session

from app.models import District


_NON_FIT_NAME_HINTS = re.compile(
    r"\b(academy|charter\s+collective|microschool|learning\s+pod)\b",
    re.IGNORECASE,
)
_NON_FIT_ENROLLMENT_CEILING = 1000


@dataclass(frozen=True)
class SweepReport:
    duplicates_marked: int
    non_fit_marked: int
    notes: List[str]


def sweep(db: Session) -> SweepReport:
    notes: List[str] = []
    duplicates = _mark_duplicates(db, notes)
    non_fit = _mark_non_fit(db, notes)
    db.commit()
    return SweepReport(duplicates_marked=duplicates, non_fit_marked=non_fit, notes=notes)


def _mark_duplicates(db: Session, notes: List[str]) -> int:
    """Group by (name_normalized, state); within each group, oldest wins."""
    rows: List[District] = (
        db.query(District)
        .filter(District.name_normalized.isnot(None))
        .filter(District.status.notin_(["duplicate"]))
        .all()
    )
    by_key: dict[tuple[str, str | None], list[District]] = {}
    for d in rows:
        if not d.name_normalized:
            continue
        key = (d.name_normalized, (d.state or "").upper() or None)
        by_key.setdefault(key, []).append(d)

    marked = 0
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda d: d.ingested_at)
        keeper = group[0]
        for dup in group[1:]:
            if dup.status == "duplicate" and dup.duplicate_of_external_id == keeper.external_id:
                continue  # already settled
            dup.status = "duplicate"
            dup.duplicate_of_external_id = keeper.external_id
            notes.append(f"{dup.external_id} → duplicate_of {keeper.external_id} ({key[0]}/{key[1]})")
            marked += 1
    return marked


def _mark_non_fit(db: Session, notes: List[str]) -> int:
    rows = (
        db.query(District)
        .filter(District.status.notin_(["duplicate", "non_fit", "rejected"]))
        .all()
    )
    marked = 0
    for d in rows:
        reason = _non_fit_reason(d)
        if reason:
            d.status = "non_fit"
            d.non_fit_reason = reason
            notes.append(f"{d.external_id} → non_fit: {reason}")
            marked += 1
    return marked


def _non_fit_reason(d: District) -> str | None:
    enrollment = d.enrollment or 0
    name = d.name or ""

    name_flagged = bool(_NON_FIT_NAME_HINTS.search(name))
    too_small = 0 < enrollment < _NON_FIT_ENROLLMENT_CEILING

    if name_flagged and (too_small or enrollment == 0):
        return f"name suggests non-district org ('{_first_hint(name)}'); enrollment {enrollment or 'unknown'}"
    # Catch absurdly tiny rows even without name hint (e.g. enrollment 12)
    if 0 < enrollment < 100:
        return f"enrollment {enrollment} below viable district size"
    return None


def _first_hint(name: str) -> str:
    m = _NON_FIT_NAME_HINTS.search(name)
    return m.group(0).lower() if m else ""
