"""Public surface of the data layer.

Importing from `app.models` (not `app.models.db`) keeps the rest of the
codebase decoupled from the file layout — we could split db.py later
without touching every importer.
"""
from app.models.db import (
    Base,
    Citation,
    District,
    EmailDraft,
    Enrichment,
    SessionLocal,
    Signal,
    engine,
    get_session,
    init_db,
)
from app.models.schemas import (
    CitationOut,
    DistrictIn,
    DistrictOut,
    DistrictsBulkIn,
    EmailDraftOut,
    EnrichmentOut,
    IngestAck,
    SignalIn,
    SignalsBulkIn,
)

__all__ = [
    "Base",
    "Citation",
    "CitationOut",
    "District",
    "DistrictIn",
    "DistrictOut",
    "DistrictsBulkIn",
    "EmailDraft",
    "EmailDraftOut",
    "Enrichment",
    "EnrichmentOut",
    "IngestAck",
    "SessionLocal",
    "Signal",
    "SignalIn",
    "SignalsBulkIn",
    "engine",
    "get_session",
    "init_db",
]
