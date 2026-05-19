"""Signal matcher — exercises each strategy in isolation."""
from __future__ import annotations

from app.models import District
from app.services.normalize import normalize_district_name
from app.services.signal_matcher import resolve_signal, resolve_all_unresolved


def _make_district(db, ext_id, name, state=None, website=None):
    d = District(
        external_id=ext_id,
        name=name,
        state=state,
        website=website,
        name_normalized=normalize_district_name(name),
        status="pending",
    )
    db.add(d)
    db.commit()
    return d


def test_direct_id_match(db):
    _make_district(db, "D023", "Grandview ISD")
    result = resolve_signal({"district_id": "D023", "type": "webinar"}, db)
    assert result.external_id == "D023"
    assert result.strategy == "direct_id"
    assert result.confidence == 1.0


def test_direct_id_unknown_falls_through(db):
    _make_district(db, "D001", "Larkspur USD", website="larkspurusd.org")
    # district_id given but doesn't exist; matcher should not blindly trust it
    result = resolve_signal({"district_id": "D999"}, db)
    assert result.external_id is None
    assert result.strategy == "unmatched"
    assert "D999" in result.notes


def test_email_domain_match(db):
    _make_district(db, "D009", "Brookhaven Public Schools", website="brookhavenps.org")
    result = resolve_signal(
        {"attendee_email": "rachel.kim@brookhavenps.org"}, db,
    )
    assert result.external_id == "D009"
    assert result.strategy == "email_domain"
    assert result.confidence == 0.95


def test_email_domain_ignores_generic(db):
    _make_district(db, "D009", "Brookhaven Public Schools", website="brookhavenps.org")
    # gmail.com should never match a district
    result = resolve_signal({"attendee_email": "random@gmail.com"}, db)
    assert result.strategy == "unmatched"


def test_fuzzy_name_match_via_employer_text(db):
    _make_district(db, "D011", "Salt Lake County School District", website="slcsd.org")
    result = resolve_signal({"employer_text": "Salt Lake County SD"}, db)
    assert result.external_id == "D011"
    assert result.strategy == "fuzzy_name"
    assert result.confidence >= 0.85


def test_fuzzy_name_match_via_agency_text(db):
    _make_district(db, "D011", "Salt Lake County School District", website="slcsd.org")
    result = resolve_signal({"agency_text": "Salt Lake County School District"}, db)
    assert result.external_id == "D011"
    assert result.strategy == "fuzzy_name"


def test_unmatched_when_nothing_relevant(db):
    _make_district(db, "D001", "Larkspur USD")
    result = resolve_signal(
        {"ip_address": "73.41.x.x", "page_path": "/pricing"}, db,
    )
    assert result.external_id is None
    assert result.strategy == "unmatched"


def test_direct_id_beats_email_domain(db):
    # A district whose website would match but whose direct_id is the real one
    _make_district(db, "D009", "Brookhaven Public Schools", website="brookhavenps.org")
    _make_district(db, "D023", "Grandview ISD")
    result = resolve_signal({
        "district_id": "D023",
        "attendee_email": "x@brookhavenps.org",
    }, db)
    assert result.external_id == "D023"
    assert result.strategy == "direct_id"


def test_resolve_all_unresolved_writes_results_back(db):
    from app.models import Signal

    _make_district(db, "D023", "Grandview ISD")
    db.add(Signal(
        external_id="SIG001",
        signal_type="webinar_attendance",
        payload={"signal_id": "SIG001", "type": "webinar_attendance", "district_id": "D023"},
    ))
    db.commit()

    counts = resolve_all_unresolved(db)
    assert counts["total"] == 1
    assert counts["direct_id"] == 1

    sig = db.query(Signal).filter(Signal.external_id == "SIG001").one()
    assert sig.resolved_district_external_id == "D023"
    assert sig.match_strategy == "direct_id"
    assert sig.match_confidence == 1.0


def test_resolve_all_unresolved_is_idempotent(db):
    from app.models import Signal

    _make_district(db, "D023", "Grandview ISD")
    db.add(Signal(
        external_id="SIG001",
        signal_type="webinar_attendance",
        payload={"district_id": "D023"},
    ))
    db.commit()

    resolve_all_unresolved(db)
    second = resolve_all_unresolved(db)
    # Already resolved, so the second pass finds zero unresolved
    assert second["total"] == 0
