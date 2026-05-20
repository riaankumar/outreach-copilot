"""Sourcing service — verification + email prediction (pure logic)."""
from __future__ import annotations

from app.services.sourcing import (
    SourcedDistrict,
    SourcedPerson,
    predict_email,
    verify_sourced_district,
    _domain_from_url,
)


# ─── predict_email ────────────────────────────────────────────

def test_predict_email_firstname_lastname():
    assert predict_email("Jane Doe", "firstname.lastname@example.org") == "jane.doe@example.org"


def test_predict_email_flastname():
    assert predict_email("Jane Doe", "flastname@example.org") == "jdoe@example.org"


def test_predict_email_three_part_name_uses_first_and_last():
    assert predict_email("Maria Jose Garcia", "firstname.lastname@example.org") == "maria.garcia@example.org"


def test_predict_email_returns_none_on_missing_fields():
    assert predict_email("", "firstname.lastname@x.org") is None
    assert predict_email("Jane", "firstname.lastname@x.org") is None  # need two parts
    assert predict_email("Jane Doe", "") is None


def test_predict_email_strips_unsafe_chars():
    # firstname=jane → safe; trailing punctuation stripped
    assert predict_email("Jane O'Doe", "firstname.lastname@example.org") == "jane.odoe@example.org"


# ─── verify_sourced_district ──────────────────────────────────

def test_verify_flags_missing_state():
    s = SourcedDistrict(name="Some District", email_domain="example.org",
                        sped_director=SourcedPerson(name="A Person"))
    out = verify_sourced_district(s)
    assert any("missing_state" in f for f in out.flags)


def test_verify_flags_email_domain_mismatch():
    s = SourcedDistrict(
        name="Foo District", state="CA", email_domain="foodistrict.org",
        sped_director=SourcedPerson(name="A Person", email="a.person@OTHER-DOMAIN.org"),
    )
    out = verify_sourced_district(s)
    assert any("email_domain_mismatch" in f for f in out.flags)


def test_verify_flags_no_sped_leader():
    s = SourcedDistrict(name="Foo District", state="CA", email_domain="foodistrict.org")
    out = verify_sourced_district(s)
    assert any("no_sped_leader" in f for f in out.flags)


def test_verify_flags_no_email_domain():
    s = SourcedDistrict(name="Foo District", state="CA",
                        sped_director=SourcedPerson(name="X"))
    out = verify_sourced_district(s)
    assert any("no_email_domain" in f for f in out.flags)


def test_verify_predicted_emails_do_not_trigger_domain_mismatch():
    """is_predicted=True means the email was generated from the pattern; we
    should not penalize it for not matching what we scraped."""
    s = SourcedDistrict(
        name="Foo District", state="CA", email_domain="foodistrict.org",
        sped_director=SourcedPerson(name="X", email="x@other.org", is_predicted=True),
    )
    out = verify_sourced_district(s)
    assert not any("email_domain_mismatch" in f for f in out.flags)


def test_verify_confidence_high_when_everything_present():
    s = SourcedDistrict(
        name="Larkspur Unified School District", state="CA",
        website="larkspurusd.org", email_domain="larkspurusd.org",
        email_format="firstname.lastname@larkspurusd.org",
        nces_district_id="0612345",
        sped_director=SourcedPerson(name="J Smith", email="j.smith@larkspurusd.org",
                                    title="Director of Special Education"),
    )
    out = verify_sourced_district(s)
    # Domain DNS might or might not resolve in test env; allow some flag slack
    assert out.confidence >= 0.5


# ─── _domain_from_url ─────────────────────────────────────────

def test_domain_from_url_strips_protocol_and_www():
    assert _domain_from_url("https://www.example.org/contact") == "example.org"
    assert _domain_from_url("http://example.org") == "example.org"
    assert _domain_from_url("example.org") == "example.org"
