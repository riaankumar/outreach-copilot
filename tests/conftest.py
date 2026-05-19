"""Shared fixtures.

Each test gets a fresh in-memory SQLite so the dev DB is untouched and
tests run in arbitrary order. The FastAPI app's `get_session` dependency
is overridden to yield the test session, so endpoints + service functions
see the same isolated DB.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, get_session
from app.main import app


@pytest.fixture
def db_engine():
    # StaticPool + a single shared in-memory DB so every session sees the
    # same tables. Without this each new connection gets a fresh empty DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(db_engine):
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_engine):
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)

    def _override():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ─── Sample data factories ──────────────────────────────────────

SAMPLE_DISTRICTS = [
    {"district_id": "D001", "name": "Larkspur Unified School District", "state": "CA", "website": "larkspurusd.org", "enrollment": 18500, "intake_notes": "Inbound contact form 4/1"},
    {"district_id": "D006", "name": "Pinecrest Academy", "state": "FL", "website": "pinecrestacademy.com", "enrollment": 320, "intake_notes": "Inbound contact form 3/18"},
    {"district_id": "D007", "name": "St. Augustine Charter Collective", "state": "FL", "enrollment": 12},
    {"district_id": "D009", "name": "Brookhaven Public Schools", "state": "MA", "website": "brookhavenps.org", "enrollment": 6700, "intake_notes": "Whitepaper download 3/22"},
    {"district_id": "D010", "name": "Brookhaven Public Sch.", "state": "MA", "enrollment": 6800, "intake_notes": "Imported from Apollo list 4/3"},
    {"district_id": "D011", "name": "Salt Lake County School District", "state": "UT", "website": "slcsd.org", "enrollment": 41000},
    {"district_id": "D023", "name": "Grandview ISD", "state": "TX", "website": "grandviewisd.org", "enrollment": 7400, "intake_notes": "Webinar attendee 'IEP Compliance at Scale' 4/10"},
]

SAMPLE_SIGNALS = [
    # direct_id match
    {"signal_id": "SIG001", "type": "webinar_attendance", "date": "2026-03-14",
     "attendee_email": "j.peterson@grandviewisd.org", "attendee_name": "James Peterson",
     "attendee_title": "Director of Special Education", "district_id": "D023"},
    # email_domain match
    {"signal_id": "SIG002", "type": "whitepaper_download", "date": "2026-03-15",
     "attendee_email": "rachel.kim@brookhavenps.org", "attendee_company_text": "Brookhaven Public Schools"},
    # fuzzy_name match on agency_text
    {"signal_id": "SIG008", "type": "rfp_posting", "date": "2026-03-24",
     "agency_text": "Salt Lake County School District",
     "summary": "RFP-2026-014: Special Education Case Management"},
    # unmatched — only generic content
    {"signal_id": "SIG009", "type": "content_view", "date": "2026-03-25",
     "ip_address": "73.41.x.x", "page_path": "/pricing", "session_minutes": 7},
    # fuzzy_name via employer_text
    {"signal_id": "SIG005", "type": "linkedin_engagement", "date": "2026-03-20",
     "person_name": "Daniel Vasquez", "employer_text": "Salt Lake County SD",
     "engagement_type": "post_like"},
]
