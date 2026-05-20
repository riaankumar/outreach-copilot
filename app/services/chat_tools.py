"""Tools the chat copilot can call.

Each tool is a thin wrapper over existing services + ORM queries so the
behavior stays consistent with the dashboard. Tool schemas are returned
to Claude verbatim; executors all return JSON-serializable dicts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import District, EmailDraft, Signal
from app.services import email_drafter, enrichment
from app.services.signal_matcher import resolve_all_unresolved


# ─── Tool schemas (sent to Claude) ───────────────────────────────

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "list_districts",
        "description": (
            "List districts in the system with their key fields. Use this to answer "
            "scanning questions like 'which districts are pending?', 'show me Texas districts', "
            "'who has the highest fit score?'. Returns compact records."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by lifecycle status",
                    "enum": ["pending", "enriching", "enriched", "edited", "approved", "rejected", "non_fit", "duplicate"],
                },
                "state": {"type": "string", "description": "Two-letter state filter, e.g. 'TX'"},
                "min_fit": {"type": "integer", "description": "Only include districts with fit_score >= this (0-100)"},
                "limit": {"type": "integer", "description": "Max records to return. Default 30."},
                "sort_by": {
                    "type": "string",
                    "enum": ["fit", "enrollment", "signals", "name"],
                    "description": "Sort key. Default 'fit'.",
                },
            },
        },
    },
    {
        "name": "get_district",
        "description": (
            "Get one district's full record — enrichment, email draft, citations, "
            "resolved signals. Use this when the SDR asks about a specific district."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "district_id": {"type": "string", "description": "External ID like 'D023'"},
            },
            "required": ["district_id"],
        },
    },
    {
        "name": "summarize_pipeline",
        "description": (
            "Top-level counts and aggregates across all districts: by status, by state, "
            "average fit score, signal coverage. Use for 'how am I doing this week?' "
            "or 'what's the breakdown' questions."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_pipeline",
        "description": (
            "WRITE ACTION. Run signal matching → enrichment → email drafting for a single "
            "district. The Claude assistant MUST get explicit user confirmation in the "
            "conversation before calling this tool. Pass user_confirmed=true only after "
            "the user has explicitly approved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "district_id": {"type": "string"},
                "user_confirmed": {"type": "boolean", "description": "Must be true."},
            },
            "required": ["district_id", "user_confirmed"],
        },
    },
    {
        "name": "approve_draft",
        "description": (
            "WRITE ACTION. Approve a district's email draft and mark the district approved. "
            "Requires explicit user confirmation; pass user_confirmed=true only after the "
            "user has said yes in the conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "district_id": {"type": "string"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["district_id", "user_confirmed"],
        },
    },
    {
        "name": "edit_draft",
        "description": (
            "WRITE ACTION. Edit a district's email draft subject and/or body. Requires "
            "explicit user confirmation; pass user_confirmed=true only after the user has "
            "approved the new content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "district_id": {"type": "string"},
                "edited_subject": {"type": "string"},
                "edited_body": {"type": "string"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["district_id", "user_confirmed"],
        },
    },
    {
        "name": "reject_draft",
        "description": (
            "WRITE ACTION. Reject a district's email draft with a reason. Requires "
            "explicit user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "district_id": {"type": "string"},
                "reason": {"type": "string"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["district_id", "reason", "user_confirmed"],
        },
    },
]


# ─── Tool executors ──────────────────────────────────────────────

def execute_tool(name: str, args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """Dispatch to the right tool executor. Returns a JSON-serializable dict."""
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool '{name}'"}
    try:
        return fn(args, db)
    except _ConfirmationMissing as e:
        return {"error": "user_confirmed=false", "needs_confirmation": True, "message": str(e)}
    except Exception as e:  # noqa: BLE001 — surface to the model
        return {"error": str(e)}


class _ConfirmationMissing(RuntimeError):
    pass


def _require_confirmed(args: Dict[str, Any], action: str) -> None:
    if not args.get("user_confirmed"):
        raise _ConfirmationMissing(
            f"Cannot {action}: user_confirmed=true is required. Ask the user to confirm in the conversation first."
        )


# ─── Read tools ─────────────────────────────────────────────────

def _t_list_districts(args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    q = db.query(District)
    if status := args.get("status"):
        q = q.filter(District.status == status)
    if state := args.get("state"):
        q = q.filter(District.state == state.upper())

    rows: List[District] = q.all()
    if min_fit := args.get("min_fit"):
        rows = [d for d in rows if d.enrichment and (d.enrichment.fit_score or 0) >= int(min_fit)]

    sort_by = args.get("sort_by", "fit")
    if sort_by == "fit":
        rows.sort(key=lambda d: -((d.enrichment.fit_score if d.enrichment else None) or -1))
    elif sort_by == "enrollment":
        rows.sort(key=lambda d: -(d.enrollment or 0))
    elif sort_by == "signals":
        rows.sort(key=lambda d: -len(d.signals or []))
    elif sort_by == "name":
        rows.sort(key=lambda d: d.name)

    limit = int(args.get("limit", 30))
    rows = rows[:limit]

    return {
        "count": len(rows),
        "districts": [
            {
                "district_id": d.external_id,
                "name": d.name,
                "state": d.state,
                "enrollment": d.enrollment,
                "status": d.status,
                "signal_count": len(d.signals or []),
                "fit_score": d.enrichment.fit_score if d.enrichment else None,
                "draft_status": d.email_draft.status if d.email_draft else None,
            }
            for d in rows
        ],
    }


def _t_get_district(args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    d = db.query(District).filter(District.external_id == args["district_id"]).one_or_none()
    if d is None:
        return {"error": f"district {args['district_id']} not found"}

    enrich = None
    if d.enrichment:
        enrich = {
            "fit_score": d.enrichment.fit_score,
            "fit_reasoning": d.enrichment.fit_reasoning,
            "fit_breakdown": d.enrichment.fit_breakdown,
            "iep_pct": d.enrichment.iep_pct,
            "iep_count_estimate": d.enrichment.iep_count_estimate,
            "sped_director_name": d.enrichment.sped_director_name,
            "sped_director_title": d.enrichment.sped_director_title,
            "sped_director_email": d.enrichment.sped_director_email,
            "region_context": d.enrichment.region_context,
            "recent_initiatives": d.enrichment.recent_initiatives,
            "pain_points": d.enrichment.pain_points,
        }

    draft = None
    if d.email_draft:
        draft = {
            "id": d.email_draft.id,
            "status": d.email_draft.status,
            "subject": d.email_draft.edited_subject or d.email_draft.subject,
            "body": d.email_draft.edited_body or d.email_draft.body,
            "recipient_name": d.email_draft.recipient_name,
            "recipient_email": d.email_draft.recipient_email,
        }

    signals = [
        {
            "signal_id": s.external_id,
            "type": s.signal_type,
            "date": s.signal_date,
            "match_strategy": s.match_strategy,
            "match_confidence": s.match_confidence,
            "payload": s.payload,
        }
        for s in (d.signals or [])
    ]

    return {
        "district_id": d.external_id,
        "name": d.name,
        "state": d.state,
        "website": d.website,
        "enrollment": d.enrollment,
        "intake_notes": d.intake_notes,
        "status": d.status,
        "duplicate_of_external_id": d.duplicate_of_external_id,
        "non_fit_reason": d.non_fit_reason,
        "enrichment": enrich,
        "email_draft": draft,
        "signals": signals,
    }


def _t_summarize_pipeline(_args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    rows: List[District] = db.query(District).all()
    by_status: Dict[str, int] = {}
    by_state: Dict[str, int] = {}
    enrich_scores: List[int] = []
    total_signals = db.query(Signal).count()
    matched_signals = db.query(Signal).filter(Signal.resolved_district_external_id.isnot(None)).count()

    for d in rows:
        by_status[d.status] = by_status.get(d.status, 0) + 1
        if d.state:
            by_state[d.state] = by_state.get(d.state, 0) + 1
        if d.enrichment and d.enrichment.fit_score is not None:
            enrich_scores.append(d.enrichment.fit_score)

    avg_fit = round(sum(enrich_scores) / len(enrich_scores), 1) if enrich_scores else None

    return {
        "total_districts": len(rows),
        "by_status": by_status,
        "by_state_top": dict(sorted(by_state.items(), key=lambda kv: -kv[1])[:6]),
        "signals_total": total_signals,
        "signals_matched": matched_signals,
        "districts_with_enrichment": len(enrich_scores),
        "avg_fit_score": avg_fit,
        "high_fit_districts": [
            {"district_id": d.external_id, "name": d.name, "fit_score": d.enrichment.fit_score}
            for d in rows
            if d.enrichment and (d.enrichment.fit_score or 0) >= 75
        ],
    }


# ─── Write tools ───────────────────────────────────────────────

def _t_run_pipeline(args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    _require_confirmed(args, "run pipeline")
    district_id = args["district_id"]
    d = db.query(District).filter(District.external_id == district_id).one_or_none()
    if d is None:
        return {"error": f"district {district_id} not found"}
    if d.status in ("duplicate", "non_fit", "rejected"):
        return {"error": f"district is {d.status}; cannot run pipeline"}

    resolve_all_unresolved(db)
    try:
        e = enrichment.enrich_district(district_id, db)
    except enrichment.EnrichmentError as ex:
        return {"error": f"enrichment failed: {ex}"}
    try:
        draft = email_drafter.draft_email(district_id, db)
    except email_drafter.EmailDraftError as ex:
        return {"ok": True, "enriched": True, "drafted": False, "fit_score": e.fit_score,
                "warning": f"draft failed: {ex}"}
    return {
        "ok": True,
        "district_id": district_id,
        "fit_score": e.fit_score,
        "draft_subject": draft.subject,
        "draft_status": draft.status,
    }


def _t_approve_draft(args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    _require_confirmed(args, "approve draft")
    d = db.query(District).filter(District.external_id == args["district_id"]).one_or_none()
    if d is None or d.email_draft is None:
        return {"error": "district or draft not found"}
    from datetime import datetime
    d.email_draft.status = "approved"
    d.email_draft.decided_at = datetime.utcnow()
    d.status = "approved"
    db.commit()
    return {"ok": True, "district_id": d.external_id, "draft_status": "approved", "district_status": "approved"}


def _t_edit_draft(args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    _require_confirmed(args, "edit draft")
    d = db.query(District).filter(District.external_id == args["district_id"]).one_or_none()
    if d is None or d.email_draft is None:
        return {"error": "district or draft not found"}
    from datetime import datetime
    if (subj := args.get("edited_subject")) is not None:
        d.email_draft.edited_subject = subj
    if (body := args.get("edited_body")) is not None:
        d.email_draft.edited_body = body
    d.email_draft.status = "edited"
    d.email_draft.decided_at = datetime.utcnow()
    db.commit()
    return {
        "ok": True,
        "district_id": d.external_id,
        "draft_status": "edited",
        "edited_subject": d.email_draft.edited_subject,
    }


def _t_reject_draft(args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    _require_confirmed(args, "reject draft")
    d = db.query(District).filter(District.external_id == args["district_id"]).one_or_none()
    if d is None or d.email_draft is None:
        return {"error": "district or draft not found"}
    from datetime import datetime
    d.email_draft.status = "rejected"
    d.email_draft.rejection_reason = args["reason"]
    d.email_draft.decided_at = datetime.utcnow()
    d.status = "rejected"
    db.commit()
    return {"ok": True, "district_id": d.external_id, "draft_status": "rejected"}


_DISPATCH = {
    "list_districts": _t_list_districts,
    "get_district": _t_get_district,
    "summarize_pipeline": _t_summarize_pipeline,
    "run_pipeline": _t_run_pipeline,
    "approve_draft": _t_approve_draft,
    "edit_draft": _t_edit_draft,
    "reject_draft": _t_reject_draft,
}


READ_ONLY_TOOLS = {"list_districts", "get_district", "summarize_pipeline"}
WRITE_TOOLS = {"run_pipeline", "approve_draft", "edit_draft", "reject_draft"}
