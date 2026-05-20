"""Pipeline view — send-priority computation.

Pure function tests for the segmentation that powers the SDR's "who do I
send to today" view.
"""
from __future__ import annotations

from datetime import date

from app.services.pipeline_view import compute_send_priority


_TODAY = date(2026, 5, 19)


def _p(**kw):
    """Shorthand: compute_send_priority with sensible defaults."""
    return compute_send_priority(
        status=kw.get("status", "approved"),
        fit_score=kw.get("fit_score"),
        signal_dates=kw.get("signal_dates", []),
        today=_TODAY,
    )


def test_non_approved_returns_none():
    """Only approved districts get a send priority — pending, enriched, etc.
    aren't in the send queue."""
    assert _p(status="pending", fit_score=90, signal_dates=["2026-05-15"]) is None
    assert _p(status="enriched", fit_score=90) is None
    assert _p(status="rejected", fit_score=90) is None
    assert _p(status="duplicate") is None
    assert _p(status="non_fit") is None


def test_recent_signal_promotes_to_send_today():
    """A signal in the last 14 days promotes to send_today regardless of fit."""
    assert _p(fit_score=40, signal_dates=["2026-05-10"]) == "send_today"  # 9 days ago
    assert _p(fit_score=20, signal_dates=["2026-05-05"]) == "send_today"  # 14 days ago


def test_strong_fit_lands_send_today_even_without_recent_signal():
    assert _p(fit_score=90) == "send_today"
    assert _p(fit_score=75, signal_dates=["2026-01-01"]) == "send_today"  # old signal but strong fit


def test_moderate_fit_or_warm_signal_is_this_week():
    assert _p(fit_score=60) == "this_week"
    assert _p(fit_score=20, signal_dates=["2026-04-10"]) == "this_week"  # 39 days ago, in warm window


def test_weak_fit_and_no_warm_signal_is_later():
    assert _p(fit_score=20) == "later"
    assert _p(fit_score=20, signal_dates=["2025-12-01"]) == "later"
    assert _p(fit_score=None) == "later"


def test_no_signal_dates_at_all():
    """A district approved on fit alone, no signals — falls into the fit-based band."""
    assert _p(fit_score=90, signal_dates=[]) == "send_today"
    assert _p(fit_score=50, signal_dates=[]) == "this_week"
    assert _p(fit_score=10, signal_dates=[]) == "later"


def test_malformed_signal_date_ignored():
    """Garbage dates don't crash the priority calc."""
    assert _p(fit_score=80, signal_dates=[None, "not a date", "2026-05-10"]) == "send_today"
    assert _p(fit_score=40, signal_dates=[None, "garbage"]) == "later"


def test_most_recent_signal_wins():
    """When a district has multiple signals, the most recent drives priority."""
    # Old + recent → recent wins → send_today
    assert _p(fit_score=20, signal_dates=["2024-01-01", "2026-05-15"]) == "send_today"
    # Just old signals → fall back to fit
    assert _p(fit_score=20, signal_dates=["2024-01-01", "2024-06-01"]) == "later"


def test_band_thresholds_are_inclusive():
    """Document the exact threshold behavior (boundary regressions are
    expensive to debug later)."""
    # Recency 14 days exactly → still send_today
    assert _p(fit_score=10, signal_dates=["2026-05-05"]) == "send_today"
    # Recency 15 days → past today threshold
    assert _p(fit_score=10, signal_dates=["2026-05-04"]) != "send_today"
    # Fit 75 → send_today
    assert _p(fit_score=75) == "send_today"
    # Fit 74 → drops to this_week
    assert _p(fit_score=74) == "this_week"
    # Fit 50 → this_week boundary
    assert _p(fit_score=50) == "this_week"
    # Fit 49 → later
    assert _p(fit_score=49) == "later"
