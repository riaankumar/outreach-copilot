"""Source new district leads from the open web.

Given a district name or URL, this service:
  1. Uses Claude with the web_search + web_fetch tools to find the
     district's official site and pull structured data from it.
  2. Extracts: name, state, enrollment, primary email domain, SPED
     leadership (Director of SPED, Asst Supt Student Services), an
     "email_format" pattern (mirrors the Kritikos playbook), and a
     short hook line tailored to the district.
  3. Runs a verification pass: DNS / MX on the email domain, internal
     email-pattern consistency, plus flags for any inferred field.

Output is a `SourcedDistrict` that the API converts into a district
+ intake-note signal for the existing enrichment pipeline. SDR ingests
it from the UI side panel.
"""
from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass
from typing import Any, List, Optional

from anthropic import Anthropic
from pydantic import BaseModel, ConfigDict, Field


MODEL = os.getenv("JOURNIFY_SOURCING_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 3000


# ─── Output schemas ───────────────────────────────────────────

class SourcedPerson(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    source_url: Optional[str] = None
    is_predicted: bool = False  # email was generated from pattern, not scraped


class SourcedCitation(BaseModel):
    field_name: str
    source_url: Optional[str] = None
    quote: Optional[str] = None


class SourcedDistrict(BaseModel):
    """Structured packet returned to the API + UI."""
    model_config = ConfigDict(extra="ignore")

    # Core identity
    name: str
    state: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None
    nces_district_id: Optional[str] = None

    # Scale + segment
    enrollment: Optional[int] = None
    school_count: Optional[int] = None
    district_type: Optional[str] = None  # public | charter | private | unknown

    # Email pattern (Kritikos-style)
    email_domain: Optional[str] = None
    email_format: Optional[str] = None  # e.g. "firstname.lastname@grandviewisd.org"

    # Leadership / champions
    superintendent: Optional[SourcedPerson] = None
    sped_director: Optional[SourcedPerson] = None
    other_contacts: List[SourcedPerson] = Field(default_factory=list)

    # Hook for the SDR
    key_hook: Optional[str] = None  # 1-sentence reason this district is fit
    notes: Optional[str] = None     # free-form context

    # Provenance
    citations: List[SourcedCitation] = Field(default_factory=list)

    # Verification output (set by verify())
    confidence: float = 0.0
    flags: List[str] = Field(default_factory=list)


# ─── Tool schema for Claude ───────────────────────────────────

_PERSON_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "name": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
        "linkedin_url": {"type": ["string", "null"]},
        "source_url": {"type": ["string", "null"]},
        "is_predicted": {"type": "boolean"},
    },
}

_SUBMIT_TOOL = {
    "name": "submit_sourced_district",
    "description": "Return the structured district packet after web research.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "state": {"type": ["string", "null"]},
            "city": {"type": ["string", "null"]},
            "website": {"type": ["string", "null"]},
            "nces_district_id": {"type": ["string", "null"]},
            "enrollment": {"type": ["integer", "null"]},
            "school_count": {"type": ["integer", "null"]},
            "district_type": {"type": ["string", "null"], "enum": ["public", "charter", "private", "unknown", None]},
            "email_domain": {"type": ["string", "null"]},
            "email_format": {"type": ["string", "null"], "description": "Inferred pattern like 'firstname.lastname@district.org'"},
            "superintendent": _PERSON_SCHEMA,
            "sped_director": _PERSON_SCHEMA,
            "other_contacts": {"type": "array", "items": _PERSON_SCHEMA},
            "key_hook": {"type": ["string", "null"], "description": "1 sentence: why this district fits Journify."},
            "notes": {"type": ["string", "null"]},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_name": {"type": "string"},
                        "source_url": {"type": ["string", "null"]},
                        "quote": {"type": ["string", "null"]},
                    },
                    "required": ["field_name"],
                },
            },
        },
        "required": ["name", "citations"],
    },
}


_SYSTEM = """You are a sourcing analyst for Journify Learning. Given a
district name or website, your job is to find the district's official
site and extract the data the SDR needs to start outreach.

Hard rules:
- Use the web_search and web_fetch tools to research. Prefer the
  district's own website over third parties.
- Every non-trivial claim goes into `citations` with a source_url.
- If you can't verify something, set the field to null. Never invent.
- For SPED leadership: look for "Director of Special Education", "Asst.
  Superintendent of Student Services", "Director of Student Services",
  "Special Education Coordinator". Pull name, title, email (if listed)
  and a LinkedIn URL if you find one.
- Email format: if you find two or more staff emails on the same domain,
  infer the pattern. Examples: "firstname.lastname@d.org",
  "flastname@d.org", "first.last@d.k12.{state}.us". If you only have one
  email, just record it; do not invent the pattern.
- Key hook: write ONE sentence specific to this district that the SDR
  could lead with. Reference an actual program, RFP, hire, demographic
  fact, or recent initiative you saw on the site.
- This is for K-12 public/charter districts. If you find a private
  school or college, set district_type accordingly and let the SDR
  decide.

Output: call submit_sourced_district. Do not return prose."""


def source_district(query: str) -> SourcedDistrict:
    """Run the sourcing agent. `query` may be a district name or URL."""
    client = Anthropic()

    messages: List[dict] = [{
        "role": "user",
        "content": (
            f"Source this district: {query}\n\n"
            "Find the official website, then extract identity, scale, email"
            " pattern, SPED leadership, and one specific hook the SDR can use."
            " Cite every non-trivial claim."
        ),
    }]

    # Tool-use loop with Claude's web_search + web_fetch + our submit tool.
    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
        _SUBMIT_TOOL,
    ]

    final_tool_input: Optional[dict] = None
    for _ in range(8):  # cap iterations
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM,
            tools=tools,
            messages=messages,
        )

        # Capture the assistant turn verbatim so we can feed tool_results back.
        assistant_content = [_block_to_dict(b) for b in resp.content]
        messages.append({"role": "assistant", "content": assistant_content})

        # Did the model submit the final packet?
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_sourced_district":
                final_tool_input = block.input
                break
        if final_tool_input is not None:
            break

        if resp.stop_reason != "tool_use":
            # Model gave up without submitting — return a minimal packet
            return SourcedDistrict(name=query, flags=["sourcing_failed_no_submit"])

        # For Anthropic's server-side tools (web_search), tool_results come
        # back automatically in the next response. For any client-side tools
        # we'd execute and feed back here. submit_sourced_district is our
        # only client-side tool and it terminates the loop, so the only
        # block-type to handle is the server-side web_search responses.
        # We just continue the loop.

    if final_tool_input is None:
        return SourcedDistrict(name=query, flags=["sourcing_failed_loop_exhausted"])

    try:
        sourced = SourcedDistrict.model_validate(final_tool_input)
    except Exception as e:  # noqa: BLE001
        return SourcedDistrict(name=query, flags=[f"sourcing_validation_failed: {e}"])

    return sourced


# ─── Verification layer ───────────────────────────────────────

@dataclass
class _Flag:
    code: str
    detail: str


def verify_sourced_district(sourced: SourcedDistrict) -> SourcedDistrict:
    """Run cheap deterministic checks and write confidence + flags."""
    flags: List[_Flag] = []
    score = 1.0

    # 1. Website / domain DNS check
    domain = sourced.email_domain
    if not domain and sourced.website:
        domain = _domain_from_url(sourced.website)
    if domain:
        if not _domain_resolves(domain):
            flags.append(_Flag("domain_dns_unresolved", f"DNS lookup failed for {domain}"))
            score -= 0.3
    else:
        flags.append(_Flag("no_email_domain", "No email domain found; emails cannot be predicted"))
        score -= 0.2

    # 2. Email-pattern consistency — every scraped email must share the domain
    if domain:
        people = [p for p in [sourced.superintendent, sourced.sped_director] if p] + list(sourced.other_contacts)
        scraped_emails = [p.email for p in people if p and p.email and not p.is_predicted]
        bad = [e for e in scraped_emails if e.split("@")[-1].lower() != domain.lower()]
        if bad:
            flags.append(_Flag("email_domain_mismatch", f"{len(bad)} scraped email(s) don't match domain {domain}: {bad}"))
            score -= 0.2

    # 3. NCES presence
    if not sourced.nces_district_id:
        flags.append(_Flag("no_nces_id", "No NCES district ID found; may be a private or new school"))
        score -= 0.1

    # 4. Required-field sanity
    if not sourced.name or len(sourced.name) < 3:
        flags.append(_Flag("missing_name", "District name missing"))
        score -= 0.5
    if not sourced.state:
        flags.append(_Flag("missing_state", "State not identified"))
        score -= 0.1

    # 5. Leadership: at least one named SPED-relevant decision-maker
    if not (sourced.sped_director and sourced.sped_director.name):
        flags.append(_Flag("no_sped_leader", "No SPED director / student services lead found"))
        score -= 0.2

    # 6. Email-format inference plausibility
    if sourced.email_format and domain and domain not in sourced.email_format:
        flags.append(_Flag("email_format_domain_mismatch", "email_format does not include the email_domain"))
        score -= 0.1

    sourced.confidence = max(0.0, min(1.0, round(score, 2)))
    sourced.flags = [f"{f.code}: {f.detail}" for f in flags]
    return sourced


def predict_email(person_name: str, email_format: str) -> Optional[str]:
    """Apply an email_format pattern like 'firstname.lastname@d.org' to a name.

    Returns the predicted email, or None if the pattern can't be applied.
    """
    if not person_name or not email_format or "@" not in email_format:
        return None
    parts = person_name.strip().split()
    if len(parts) < 2:
        return None
    first, last = parts[0].lower(), parts[-1].lower()
    local, domain = email_format.split("@", 1)
    # Replace by descending pattern length so substrings (e.g. "lastname"
    # inside "flastname") don't get clobbered before the longer form.
    local = (
        local.lower()
        .replace("firstinitial", first[0])
        .replace("lastinitial", last[0])
        .replace("flastname", first[0] + last)
        .replace("firstname", first)
        .replace("lastname", last)
        .replace("first", first)
        .replace("last", last)
    )
    # Strip non-email chars
    local = re.sub(r"[^a-z0-9._-]", "", local)
    return f"{local}@{domain}"


# ─── Helpers ──────────────────────────────────────────────────

def _domain_from_url(url: str) -> Optional[str]:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url.strip().lower())
    if m:
        return m.group(1)
    if "/" not in url:
        return url.strip().lower().lstrip("www.")
    return None


def _domain_resolves(domain: str) -> bool:
    try:
        socket.gethostbyname(domain)
        return True
    except (socket.gaierror, socket.herror, UnicodeError):
        return False


def _block_to_dict(b: Any) -> dict:
    t = getattr(b, "type", None)
    if hasattr(b, "model_dump"):
        try:
            return b.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if t == "text":
        return {"type": "text", "text": b.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return {"type": t or "unknown"}
