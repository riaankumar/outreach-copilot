"""Resolve intent signals to districts.

Strategies, tried in order (highest confidence first):
1. direct_id     — signal carries an explicit district_id field (1.00)
2. email_domain  — attendee_email's domain matches district.website (0.95)
3. fuzzy_name    — RapidFuzz WRatio against normalized district name (0.50–1.00 scaled)

Anything below 0.85 fuzzy confidence is left unmatched — false-positive
SDR outreach is worse than a missed signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models import District, Signal
from app.services.normalize import normalize_district_name


_FUZZY_ACCEPT_THRESHOLD = 85  # WRatio 0–100

# Free-text fields a signal might use to name its district. Order matters
# only as a tie-breaker — we take the highest-scoring match across all.
_FREE_TEXT_FIELDS = (
    "attendee_company_text",
    "employer_text",
    "referred_district_text",
    "agency_text",
    "submitter_company_text",
)

# Generic email domains we never use to identify a district.
_GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
}


@dataclass(frozen=True)
class MatchResult:
    external_id: Optional[str]
    confidence: float
    strategy: str   # "direct_id" | "email_domain" | "fuzzy_name" | "unmatched"
    notes: str


def resolve_signal(payload: dict, db: Session) -> MatchResult:
    """Pure-ish: reads districts, returns a MatchResult. No mutation."""
    # 1. direct_id
    direct = payload.get("district_id")
    if isinstance(direct, str) and direct.strip():
        if db.query(District).filter(District.external_id == direct).count():
            return MatchResult(direct, 1.0, "direct_id", f"explicit district_id={direct}")
        # ID provided but not found — fall through, don't trust it blindly
        notes_prefix = f"explicit district_id={direct} not found; "
    else:
        notes_prefix = ""

    # 2. email_domain
    email = _first_email(payload)
    if email:
        domain = _domain_root(email)
        if domain and domain not in _GENERIC_EMAIL_DOMAINS:
            hit = (
                db.query(District)
                .filter(District.website.isnot(None))
                .filter(District.website.contains(domain))
                .first()
            )
            if hit is None:
                # also try the reverse — district.website is a subdomain/path of the email's host
                hit = _district_by_website_match(domain, db)
            if hit is not None:
                return MatchResult(
                    hit.external_id, 0.95, "email_domain",
                    f"{notes_prefix}email {email} → domain {domain} → {hit.external_id}",
                )

    # 3. fuzzy_name
    candidate_text = _best_free_text(payload)
    if candidate_text:
        target = normalize_district_name(candidate_text)
        if target:
            best_district, best_score = _best_fuzzy_match(target, db)
            if best_district is not None and best_score >= _FUZZY_ACCEPT_THRESHOLD:
                return MatchResult(
                    best_district.external_id,
                    round(best_score / 100, 2),
                    "fuzzy_name",
                    f"{notes_prefix}'{candidate_text}' → '{target}' ≈ '{best_district.name_normalized}' (score {best_score})",
                )
            if best_district is not None:
                return MatchResult(
                    None, 0.0, "unmatched",
                    f"{notes_prefix}closest fuzzy: '{best_district.name_normalized}' score {best_score} (<{_FUZZY_ACCEPT_THRESHOLD})",
                )

    return MatchResult(None, 0.0, "unmatched", f"{notes_prefix}no resolvable identifier")


def resolve_all_unresolved(db: Session) -> dict:
    """Walk every signal without a resolution, resolve, and persist.

    Idempotent — re-resolves anything currently unmatched, leaves matched
    signals alone (re-ingestion already clears them).
    """
    counts = {"direct_id": 0, "email_domain": 0, "fuzzy_name": 0, "unmatched": 0}
    rows = (
        db.query(Signal)
        .filter(Signal.resolved_district_external_id.is_(None))
        .all()
    )
    for sig in rows:
        result = resolve_signal(sig.payload or {}, db)
        sig.resolved_district_external_id = result.external_id
        sig.match_confidence = result.confidence
        sig.match_strategy = result.strategy
        sig.match_notes = result.notes
        counts[result.strategy] = counts.get(result.strategy, 0) + 1
    db.commit()
    return {"total": len(rows), **counts}


# ─── Helpers ───────────────────────────────────────────────────────────

def _first_email(payload: dict) -> Optional[str]:
    for key in ("attendee_email", "submitter_email", "person_email", "email"):
        v = payload.get(key)
        if isinstance(v, str) and "@" in v:
            return v.strip().lower()
    return None


def _domain_root(email: str) -> str:
    """Return the bare apex domain, e.g. 'jane@sub.foo.k12.tx.us' → 'foo.k12.tx.us'.

    We compare against the district.website string with `contains`, which
    handles common K-12 subdomain shapes ('mail.foo.org', 'foo.k12.tx.us').
    """
    _, _, host = email.partition("@")
    return host.strip().lower()


def _district_by_website_match(host: str, db: Session) -> Optional[District]:
    """Fallback: scan districts whose .website occurs in `host` or vice-versa."""
    candidates = db.query(District).filter(District.website.isnot(None)).all()
    for d in candidates:
        w = (d.website or "").lower()
        if not w:
            continue
        if w in host or host in w:
            return d
    return None


def _best_free_text(payload: dict) -> Optional[str]:
    for k in _FREE_TEXT_FIELDS:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # last resort: 'message_text' or 'context' — sometimes the only locator
    for k in ("context", "message_text"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _best_fuzzy_match(target: str, db: Session) -> tuple[Optional[District], float]:
    best: Optional[District] = None
    best_score: float = 0.0
    rows = db.query(District).filter(District.name_normalized.isnot(None)).all()
    for d in rows:
        if not d.name_normalized:
            continue
        score = fuzz.WRatio(target, d.name_normalized)
        # token_set_ratio handles "salt lake county sd" ↔ "salt lake"
        score = max(score, fuzz.token_set_ratio(target, d.name_normalized))
        if score > best_score:
            best_score = score
            best = d
    return best, best_score


# Lightweight pattern used in tests / debug output
_DOMAIN_RE = re.compile(r"^[a-z0-9.-]+$")
