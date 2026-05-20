"""Email style enforcement — strict rules for what ships to a real prospect.

Two layers of defense:
  1. The system prompt bans em dashes and AI-slop phrases.
  2. clean_subject / clean_body post-process anything that slips through.

These tests exercise the post-processors directly with adversarial input
that the prompt is supposed to prevent.
"""
from __future__ import annotations

import pytest

from app.services.email_drafter import (
    _BANNED_PHRASES,
    clean_body,
    clean_subject,
    word_count,
)


# ─── clean_subject ──────────────────────────────────────────

def test_subject_removes_em_dash():
    assert clean_subject("IEP compliance at scale — for Grandview") == "IEP compliance at scale. for Grandview"


def test_subject_removes_en_dash():
    assert clean_subject("Two webinars – a whitepaper") == "Two webinars. a whitepaper"


def test_subject_collapses_whitespace():
    assert clean_subject("RFP   2026-014    question") == "RFP 2026-014 question"


def test_subject_leaves_hyphens_alone():
    """Hyphens in compound words / IDs must survive."""
    assert "RFP-2026-014" in clean_subject("RFP-2026-014 question")
    assert "audit-prep" in clean_subject("audit-prep this Friday")


# ─── clean_body ──────────────────────────────────────────────

def test_body_replaces_sentence_separator_em_dash_with_period():
    raw = "You attended twice this spring — March and April."
    out = clean_body(raw)
    assert "—" not in out
    assert out == "You attended twice this spring. March and April."


def test_body_replaces_inline_em_dash_with_comma():
    """When the dash is hugging a word ('thing—next'), use a comma so we
    don't strand the second clause."""
    raw = "We help SPED teams—at districts like yours—save hours."
    out = clean_body(raw)
    assert "—" not in out
    assert "SPED teams, at districts" in out


def test_body_handles_multiple_dashes():
    raw = "Three things — speed, consistency, audit-readiness — matter most."
    out = clean_body(raw)
    assert "—" not in out


def test_body_preserves_paragraph_breaks():
    raw = "First paragraph.\n\nSecond paragraph — with a dash.\n\nThird."
    out = clean_body(raw)
    assert "\n\n" in out
    assert "—" not in out


def test_body_strips_trailing_whitespace_per_line():
    raw = "Hi James,   \n\nBody line.   "
    out = clean_body(raw)
    assert "Hi James,\n" in out
    assert not out.endswith(" ")


# ─── Word count guard ──────────────────────────────────────

def test_word_count_basic():
    assert word_count("one two three") == 3
    assert word_count("") == 0
    assert word_count("   ") == 0


def test_email_under_word_cap_after_cleaning():
    """A real spec-compliant draft should clean to ≤ 100 words."""
    sample = (
        "Hi James, two of your team registered for our IEP Compliance webinar "
        "in March and April, and someone pulled the goal-library whitepaper "
        "last week. At a thousand IEPs across Grandview, that pattern usually "
        "means audit prep is starting to eat weekends. Most SPED teams your "
        "size get back two to three hours per IEP and finish quarterly progress "
        "on a Friday afternoon. Worth twenty minutes next Tuesday or Thursday "
        "morning?"
    )
    assert word_count(clean_body(sample)) <= 100


# ─── Banned phrase enumeration ──────────────────────────────

def test_banned_phrases_list_is_comprehensive():
    """Smoke test: make sure the well-known AI-slop tells are all listed."""
    for must_be_banned in ["I hope this finds you well", "circle back", "reach out", "happy to share"]:
        assert must_be_banned in _BANNED_PHRASES, f"missing from _BANNED_PHRASES: {must_be_banned}"


# ─── Integration with the drafter persistence path ─────────

def test_drafter_pipeline_strips_em_dashes(client, monkeypatch):
    """If the LLM somehow slips an em dash through, the persisted email is
    still clean. Mock returns a deliberately-dirty draft; we assert the
    persisted subject and body are clean."""
    from app.services import email_drafter, enrichment
    from app.services.email_drafter import _DraftLLMOut, _CitationLLM
    from tests.conftest import SAMPLE_DISTRICTS, SAMPLE_SIGNALS
    from tests.test_enrichment import _fake_enrichment_response

    def _dirty(*_a, **_kw):
        return _DraftLLMOut(
            recipient_name="James Peterson",
            recipient_title="Director of Special Education",
            recipient_email="j.peterson@grandviewisd.org",
            subject="IEP compliance — for Grandview",
            body=(
                "Hi James,\n\n"
                "I noticed your team registered for our IEP webinar — twice this spring — "
                "and pulled the whitepaper.\n\n"
                "Either way, I'd love to be a resource."
            ),
            hook_summary="Two webinar attendances",
            citations=[_CitationLLM(field_name="hook", source_type="signal", source_signal_external_id="SIG001", confidence=1.0)],
        )

    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    monkeypatch.setattr(email_drafter, "_call_claude", _dirty)
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})
    client.post("/api/districts/D023/run-pipeline")

    draft = client.get("/api/districts/D023").json()["email_draft"]
    assert "—" not in draft["subject"]
    assert "—" not in draft["body"]
    # The "Either way" / "I'd love" / "I noticed" filler isn't stripped by
    # post-processing on purpose — that's the prompt's job. But the dash
    # post-process IS the safety net for the most common AI tell.
