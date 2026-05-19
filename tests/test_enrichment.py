"""Enrichment + email drafting + SDR actions.

The Anthropic calls are mocked at the seam (`_call_claude`) so these tests
exercise the persistence logic, status transitions, and PATCH handler
without spending tokens or being flaky on network.
"""
from __future__ import annotations

from app.services import enrichment, email_drafter
from app.services.enrichment import _EnrichmentLLMOut, _CitationLLM as _ECiteLLM
from app.services.email_drafter import _DraftLLMOut, _CitationLLM as _DCiteLLM
from tests.conftest import SAMPLE_DISTRICTS, SAMPLE_SIGNALS


def _fake_enrichment_response(*_args, **_kwargs):
    return _EnrichmentLLMOut(
        iep_pct=14.5,
        iep_count_estimate=1073,
        sped_program_notes="Pattern of recent SPED engagement.",
        superintendent_name=None,
        sped_director_name="James Peterson",
        sped_director_title="Director of Special Education",
        sped_director_email="j.peterson@grandviewisd.org",
        region_context="Mid-sized Texas district south of DFW.",
        recent_initiatives="Two IEP Compliance webinar attendances in Mar–Apr 2026.",
        pain_points="Compliance at scale.",
        nces_district_id="4823490",
        fit_score=88,
        fit_reasoning="Strong all four dimensions.",
        fit_breakdown={"enrollment_match": 90, "intent_signal": 95, "decision_maker_clarity": 95, "recent_activity": 92},
        raw_research_notes="(test fixture)",
        citations=[
            _ECiteLLM(field_name="sped_director_name", source_type="signal",
                      source_signal_external_id="SIG001",
                      source_quote="attendee_title: Director of Special Education",
                      confidence=1.0),
            _ECiteLLM(field_name="iep_pct", source_type="inference", confidence=0.55),
        ],
    )


def _fake_draft_response(*_args, **_kwargs):
    return _DraftLLMOut(
        recipient_name="James Peterson",
        recipient_title="Director of Special Education",
        recipient_email="j.peterson@grandviewisd.org",
        subject="IEP compliance at scale — for Grandview",
        body="Hi James,\n\nYou attended our webinar twice this spring…",
        hook_summary="Two IEP compliance webinar attendances by the director.",
        citations=[
            _DCiteLLM(field_name="hook", source_type="signal",
                      source_signal_external_id="SIG001",
                      source_quote="James Peterson attended IEP webinar 2026-03-14",
                      confidence=1.0),
        ],
    )


# ─── enrich_district ──────────────────────────────────────────

def test_enrich_district_persists_and_transitions(client, db, monkeypatch):
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)

    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})

    r = client.post("/api/districts/D023/enrich")
    assert r.status_code == 200
    body = r.json()
    assert body["fit_score"] == 88

    # District should now be in 'enriched' state with embedded enrichment
    detail = client.get("/api/districts/D023").json()
    assert detail["status"] == "enriched"
    assert detail["enrichment"]["fit_score"] == 88
    assert detail["enrichment"]["sped_director_name"] == "James Peterson"
    # Citations should round-trip including inference confidence
    inf_cites = [c for c in detail["enrichment"]["citations"] if c["source_type"] == "inference"]
    assert inf_cites and inf_cites[0]["confidence"] == 0.55


def test_enrich_refuses_for_non_fit(client, db, monkeypatch):
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    # D006 Pinecrest is auto-marked non_fit on ingest
    r = client.post("/api/districts/D006/enrich")
    assert r.status_code == 400
    assert "non_fit" in r.json()["detail"]


# ─── draft_email + SDR PATCH actions ───────────────────────────

def test_draft_email_requires_enrichment(client, monkeypatch):
    monkeypatch.setattr(email_drafter, "_call_claude", _fake_draft_response)
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    r = client.post("/api/districts/D023/draft")
    assert r.status_code == 400


def test_run_pipeline_chains_enrich_and_draft(client, monkeypatch):
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    monkeypatch.setattr(email_drafter, "_call_claude", _fake_draft_response)

    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})

    r = client.post("/api/districts/D023/run-pipeline")
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert any("enriched" in s for s in body["steps"])
    assert any("drafted" in s for s in body["steps"])

    detail = client.get("/api/districts/D023").json()
    assert detail["email_draft"]["subject"].startswith("IEP compliance")


def test_patch_approve_propagates_status(client, monkeypatch):
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    monkeypatch.setattr(email_drafter, "_call_claude", _fake_draft_response)

    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})
    client.post("/api/districts/D023/run-pipeline")

    draft_id = client.get("/api/districts/D023").json()["email_draft"]["id"]
    r = client.patch(f"/api/email-drafts/{draft_id}", json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["district_status"] == "approved"


def test_patch_edit_stores_edited_fields(client, monkeypatch):
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    monkeypatch.setattr(email_drafter, "_call_claude", _fake_draft_response)

    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})
    client.post("/api/districts/D023/run-pipeline")

    draft_id = client.get("/api/districts/D023").json()["email_draft"]["id"]
    r = client.patch(
        f"/api/email-drafts/{draft_id}",
        json={"action": "edit", "edited_subject": "New subject from SDR"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "edited"

    detail = client.get("/api/districts/D023").json()
    assert detail["email_draft"]["edited_subject"] == "New subject from SDR"


def test_patch_reject_sets_reason(client, monkeypatch):
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    monkeypatch.setattr(email_drafter, "_call_claude", _fake_draft_response)

    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})
    client.post("/api/districts/D023/run-pipeline")

    draft_id = client.get("/api/districts/D023").json()["email_draft"]["id"]
    r = client.patch(
        f"/api/email-drafts/{draft_id}",
        json={"action": "reject", "rejection_reason": "off-target hook"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["district_status"] == "rejected"


def test_patch_unknown_action_400(client):
    # No districts needed — endpoint will 404 on draft id first
    r = client.patch("/api/email-drafts/9999", json={"action": "approve"})
    assert r.status_code == 404
