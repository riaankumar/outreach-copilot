"""Derived send-priority for the SDR's pipeline view.

The spec asks for approved districts to be 'grouped, sorted, or segmented in a
way that makes the SDR's "who do I send to today" decision faster'. This module
computes a single bucket per district based on:

    - fit_score (the AI's confidence in the lead)
    - most-recent matched signal date (recency = warmth)

Bands:
    - send_today: a clear "do this next" bucket
    - this_week:  do soon, not urgent
    - later:      keep in the pipeline but don't prioritize

Pure functions, easy to unit-test and override.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Iterable, Optional


HOT_RECENCY_DAYS = 14
WARM_RECENCY_DAYS = 45
STRONG_FIT = 75
MODERATE_FIT = 50


def compute_send_priority(
    *,
    status: str,
    fit_score: Optional[int],
    signal_dates: Iterable[Optional[str]],
    today: Optional[date] = None,
) -> Optional[str]:
    """Return one of 'send_today' | 'this_week' | 'later' for approved
    districts; None for districts that aren't in the send queue yet."""
    if status != "approved":
        return None

    today = today or datetime.utcnow().date()
    most_recent_days = _days_since_most_recent(signal_dates, today)
    fit = fit_score or 0

    # A hot recent signal trumps a weak fit score — recency is a strong proxy
    # for buying intent.
    if most_recent_days is not None and most_recent_days <= HOT_RECENCY_DAYS:
        return "send_today"

    if fit >= STRONG_FIT:
        return "send_today"
    if fit >= MODERATE_FIT or (most_recent_days is not None and most_recent_days <= WARM_RECENCY_DAYS):
        return "this_week"
    return "later"


def _days_since_most_recent(signal_dates: Iterable[Optional[str]], today: date) -> Optional[int]:
    most_recent: Optional[date] = None
    for raw in signal_dates:
        parsed = _parse_iso_date(raw)
        if parsed is None:
            continue
        if most_recent is None or parsed > most_recent:
            most_recent = parsed
    if most_recent is None:
        return None
    return (today - most_recent).days


def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except (ValueError, TypeError):
        return None
