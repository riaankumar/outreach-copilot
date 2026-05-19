"""HTTP-layer integration tests via FastAPI TestClient.

Run against an in-memory SQLite (conftest.client fixture) so the dev DB is
never touched. Anthropic-calling endpoints (enrich/draft/pipeline) are
covered separately in test_enrichment.py with the LLM call mocked.
"""
from __future__ import annotations

from tests.conftest import SAMPLE_DISTRICTS, SAMPLE_SIGNALS


# ─── Ingest ────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ingest_districts_bulk_wrapped_shape(client):
    r = client.post(
        "/api/districts/bulk",
        json={"target_districts": SAMPLE_DISTRICTS},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == len(SAMPLE_DISTRICTS)
    assert body["skipped"] == 0


def test_ingest_districts_bulk_bare_list(client):
    r = client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    assert r.status_code == 200
    assert r.json()["accepted"] == len(SAMPLE_DISTRICTS)


def test_ingest_districts_skips_invalid_records(client):
    bad = [{"district_id": "X"}]  # missing required `name`
    good = [SAMPLE_DISTRICTS[0]]
    r = client.post("/api/districts/bulk", json=bad + good)
    body = r.json()
    assert body["accepted"] == 1
    assert body["skipped"] == 1
    assert len(body["skipped_reasons"]) == 1


def test_ingest_districts_is_idempotent(client):
    r1 = client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    r2 = client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    assert r1.json()["accepted"] == r2.json()["accepted"]
    assert len(client.get("/api/districts").json()) == len(SAMPLE_DISTRICTS)


def test_ingest_signals_auto_resolves(client):
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    r = client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})
    assert r.status_code == 200
    assert r.json()["accepted"] == len(SAMPLE_SIGNALS)

    # SIG001 has explicit district_id → D023; should be auto-resolved
    grandview = client.get("/api/districts/D023").json()
    assert grandview["signal_count"] >= 1


# ─── Lifecycle: duplicate + non_fit run automatically on ingest ──

def test_duplicate_marked_after_ingest(client):
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    # D010 ingested AFTER D009 → D010 is the duplicate
    d010 = client.get("/api/districts/D010").json()
    assert d010["status"] == "duplicate"
    assert d010["duplicate_of_external_id"] == "D009"


def test_non_fit_marked_after_ingest(client):
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    d006 = client.get("/api/districts/D006").json()
    assert d006["status"] == "non_fit"
    assert d006["non_fit_reason"] is not None


# ─── Read endpoints ──────────────────────────────────────────────

def test_list_districts(client):
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    r = client.get("/api/districts")
    assert r.status_code == 200
    assert len(r.json()) == len(SAMPLE_DISTRICTS)


def test_get_district_single(client):
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    r = client.get("/api/districts/D023")
    assert r.status_code == 200
    body = r.json()
    assert body["district_id"] == "D023"
    assert body["name"] == "Grandview ISD"


def test_get_district_404(client):
    r = client.get("/api/districts/DOES_NOT_EXIST")
    assert r.status_code == 404


def test_get_district_signals(client):
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})
    r = client.get("/api/districts/D023/signals")
    assert r.status_code == 200
    sigs = r.json()
    # SIG001 direct_id matches D023
    assert any(s["signal_id"] == "SIG001" for s in sigs)


# ─── Signals resolve endpoint ────────────────────────────────────

def test_signals_resolve_keeps_matched_signals_stable(client):
    """Matched signals don't churn across repeated calls; unmatched ones may
    be retried (intentional, lets a newly-ingested district catch old signals)."""
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})

    grandview_before = client.get("/api/districts/D023").json()["signal_count"]
    r = client.post("/api/signals/resolve")
    assert r.status_code == 200
    client.post("/api/signals/resolve")  # second call should not lose matches
    grandview_after = client.get("/api/districts/D023").json()["signal_count"]
    assert grandview_after == grandview_before
    assert grandview_after >= 1  # SIG001 matched via direct_id
