"""SQLAlchemy models for the District Outreach Copilot.

Design notes:
- Districts and signals are stored *as ingested* (raw fields preserved) plus
  derived columns the system computes (status, match results, etc.).
- Enrichment is a separate table so we can re-run it without destroying raw data.
- Citations live in their own table with optional FKs to enrichment OR email_draft.
  This lets us tie *individual claims* to evidence, which is the whole grounding story.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, ForeignKey, JSON, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class District(Base):
    __tablename__ = "districts"

    id = Column(Integer, primary_key=True)
    external_id = Column(String, unique=True, index=True, nullable=False)  # "D001"

    # Raw, as-ingested
    name = Column(String, nullable=False)
    state = Column(String, nullable=True)
    website = Column(String, nullable=True)
    enrollment = Column(Integer, nullable=True)
    intake_notes = Column(Text, nullable=True)

    # Normalized for matching (e.g., "brookhaven public schools" → "brookhaven")
    name_normalized = Column(String, index=True, nullable=True)

    # Lifecycle: pending | enriching | enriched | approved | rejected | non_fit | duplicate
    status = Column(String, default="pending", index=True, nullable=False)

    # Edge-case handling
    duplicate_of_external_id = Column(String, nullable=True)  # e.g., D010 → D009
    non_fit_reason = Column(Text, nullable=True)

    ingested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    enrichment = relationship("Enrichment", back_populates="district", uselist=False, cascade="all, delete-orphan")
    email_draft = relationship("EmailDraft", back_populates="district", uselist=False, cascade="all, delete-orphan")
    signals = relationship("Signal", back_populates="district")


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True)
    external_id = Column(String, unique=True, index=True, nullable=False)  # "SIG001"
    signal_type = Column(String, index=True, nullable=False)
    signal_date = Column(String, nullable=True)  # store as ISO string; signals come pre-formatted
    payload = Column(JSON, nullable=False)  # full raw record — varied shapes

    # Resolution
    resolved_district_external_id = Column(String, ForeignKey("districts.external_id"), nullable=True, index=True)
    match_confidence = Column(Float, nullable=True)  # 0.0–1.0
    match_strategy = Column(String, nullable=True)  # "direct_id" | "email_domain" | "fuzzy_name" | "unmatched"
    match_notes = Column(Text, nullable=True)  # human-readable explanation, e.g., "domain brookhavenps.org → D009"

    ingested_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    district = relationship("District", back_populates="signals")


class Enrichment(Base):
    """AI-generated portrait of the district. One row per district."""
    __tablename__ = "enrichments"

    id = Column(Integer, primary_key=True)
    district_external_id = Column(String, ForeignKey("districts.external_id"), unique=True, nullable=False)

    # Special-ed footprint
    iep_pct = Column(Float, nullable=True)
    iep_count_estimate = Column(Integer, nullable=True)
    sped_program_notes = Column(Text, nullable=True)

    # Decision-makers
    superintendent_name = Column(String, nullable=True)
    sped_director_name = Column(String, nullable=True)
    sped_director_title = Column(String, nullable=True)
    sped_director_email = Column(String, nullable=True)

    # Context
    region_context = Column(Text, nullable=True)  # "rural Texas Hill Country, growing pop"
    recent_initiatives = Column(Text, nullable=True)  # "Just passed $4M SPED bond"
    pain_points = Column(Text, nullable=True)  # inferred or from signals

    # NCES linkage if we found one
    nces_district_id = Column(String, nullable=True)

    # Fit scoring
    fit_score = Column(Integer, nullable=True)  # 0–100
    fit_reasoning = Column(Text, nullable=True)
    fit_breakdown = Column(JSON, nullable=True)  # per-criterion scores

    # Audit trail
    raw_research_notes = Column(Text, nullable=True)  # full LLM scratchpad
    model_used = Column(String, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    district = relationship("District", back_populates="enrichment")
    citations = relationship("Citation", back_populates="enrichment", cascade="all, delete-orphan")


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id = Column(Integer, primary_key=True)
    district_external_id = Column(String, ForeignKey("districts.external_id"), unique=True, nullable=False)

    recipient_name = Column(String, nullable=True)
    recipient_title = Column(String, nullable=True)
    recipient_email = Column(String, nullable=True)

    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    hook_summary = Column(Text, nullable=True)  # one-line description of the hook

    # SDR edits
    status = Column(String, default="draft", nullable=False)  # draft | approved | edited | rejected
    edited_subject = Column(String, nullable=True)
    edited_body = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    model_used = Column(String, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    decided_at = Column(DateTime, nullable=True)

    district = relationship("District", back_populates="email_draft")
    citations = relationship("Citation", back_populates="email_draft", cascade="all, delete-orphan")


class Citation(Base):
    """Polymorphic-ish: belongs to either an enrichment OR an email_draft.

    field_name describes WHAT this citation supports — e.g., 'iep_pct',
    'sped_director_name', 'hook'. That lets the UI render citations
    inline next to each claim.
    """
    __tablename__ = "citations"

    id = Column(Integer, primary_key=True)
    enrichment_id = Column(Integer, ForeignKey("enrichments.id"), nullable=True, index=True)
    email_draft_id = Column(Integer, ForeignKey("email_drafts.id"), nullable=True, index=True)

    field_name = Column(String, nullable=False)  # "iep_pct" | "hook" | "superintendent_name" ...
    source_type = Column(String, nullable=False)  # "url" | "signal" | "nces" | "intake_note" | "inference"
    source_url = Column(String, nullable=True)
    source_signal_external_id = Column(String, nullable=True)  # "SIG008"
    source_quote = Column(Text, nullable=True)  # exact text snippet
    confidence = Column(Float, nullable=True)  # 0.0–1.0

    enrichment = relationship("Enrichment", back_populates="citations")
    email_draft = relationship("EmailDraft", back_populates="citations")


# ─── Engine / session factory ──────────────────────────────────────────

_DB_PATH = "data/copilot.db"
engine = create_engine(f"sqlite:///{_DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create all tables. Idempotent — safe to call on startup."""
    import os
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yields a session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
