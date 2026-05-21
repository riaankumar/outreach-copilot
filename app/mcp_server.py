"""MCP server — exposes the District Outreach Copilot to any MCP-speaking
coding tool (Claude Code, Claude Desktop, Codex, Cursor, etc.).

Design:
- In-process. Tools call existing services + chat_tools dispatch directly,
  no HTTP hop to the FastAPI backend. One less moving part for the demo;
  also faster.
- Fresh SQLAlchemy session per tool call (open → work → commit/close).
  MCP servers are long-lived; sharing a session across calls leaks state.
- Write-tool confirmation gate is dropped here. The MCP *host* (Claude Code's
  permission system) prompts the user before any tool call, so we inject
  `user_confirmed=True` on the way through chat_tools.execute_tool. The
  in-process chat copilot keeps the gate because it has no host approval.
- Tool names are prefixed `journify_` to avoid collisions with other MCP
  servers a developer might have installed.

Run:
    uv run python -m app.mcp_server      # stdio transport
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session

from app.api.ingest import (
    _unwrap_district_payload,
    _unwrap_signal_payload,
    _upsert_districts,
    _upsert_signals,
)
from app.models import SessionLocal, Signal
from app.services import chat_tools


mcp = FastMCP("journify-district-copilot")


# ─── Session helper ────────────────────────────────────────────────

def _with_session(fn):
    """Open a fresh DB session, run fn(db), close. Commit happens inside the
    underlying service functions; we just guarantee cleanup."""
    def wrapped(*args, **kwargs):
        db: Session = SessionLocal()
        try:
            return fn(db, *args, **kwargs)
        finally:
            db.close()
    return wrapped


def _dispatch(name: str, args: Dict[str, Any], write: bool = False) -> Dict[str, Any]:
    """Route through chat_tools.execute_tool with an auto-confirmed flag for writes."""
    if write:
        args = {**args, "user_confirmed": True}
    db: Session = SessionLocal()
    try:
        return chat_tools.execute_tool(name, args, db)
    finally:
        db.close()


# ─── Read tools ────────────────────────────────────────────────────

@mcp.tool()
def journify_list_districts(
    status: Optional[str] = None,
    state: Optional[str] = None,
    min_fit: Optional[int] = None,
    sort_by: str = "fit",
    limit: int = 30,
) -> Dict[str, Any]:
    """List districts with key fields. Filter by status (pending|enriching|enriched|
    approved|rejected|non_fit|duplicate), two-letter state, or minimum fit score.
    Sort by fit|enrollment|signals|name. Returns compact records."""
    args = {"sort_by": sort_by, "limit": limit}
    if status is not None:
        args["status"] = status
    if state is not None:
        args["state"] = state
    if min_fit is not None:
        args["min_fit"] = min_fit
    return _dispatch("list_districts", args)


@mcp.tool()
def journify_get_district(district_id: str) -> Dict[str, Any]:
    """Get one district's full record: enrichment, email draft, citations,
    resolved signals. `district_id` is the external ID like 'D023'."""
    return _dispatch("get_district", {"district_id": district_id})


@mcp.tool()
def journify_summarize_pipeline() -> Dict[str, Any]:
    """Top-level counts: by status, by state, average fit score, signal coverage.
    Use for 'how am I doing this week?' style questions."""
    return _dispatch("summarize_pipeline", {})


@mcp.tool()
def journify_list_unmatched_signals() -> Dict[str, Any]:
    """List signals that couldn't be resolved to any district. These show up on
    the /unmatched page in the dashboard — they're the SDR's manual-match queue."""
    db: Session = SessionLocal()
    try:
        rows: List[Signal] = (
            db.query(Signal)
            .filter(Signal.resolved_district_external_id.is_(None))
            .all()
        )
        return {
            "count": len(rows),
            "signals": [
                {
                    "signal_id": s.external_id,
                    "type": s.signal_type,
                    "date": s.signal_date,
                    "attempts": s.match_notes,
                    "payload": s.payload,
                }
                for s in rows
            ],
        }
    finally:
        db.close()


# ─── Write tools (MCP host gates approval) ─────────────────────────

@mcp.tool()
def journify_run_pipeline(district_id: str) -> Dict[str, Any]:
    """Resolve signals → enrich → draft email for one district. Takes ~10-15s
    because of the two Claude calls. Returns fit_score and draft subject on success."""
    return _dispatch("run_pipeline", {"district_id": district_id}, write=True)


@mcp.tool()
def journify_approve_draft(district_id: str) -> Dict[str, Any]:
    """Approve the draft email for a district. Sets draft.status=approved and
    district.status=approved. Eligible for the pipeline view's send queue afterward."""
    return _dispatch("approve_draft", {"district_id": district_id}, write=True)


@mcp.tool()
def journify_edit_draft(
    district_id: str,
    edited_subject: Optional[str] = None,
    edited_body: Optional[str] = None,
) -> Dict[str, Any]:
    """Edit a district's draft subject and/or body. Persists in edited_subject /
    edited_body (the originals are preserved for audit)."""
    args: Dict[str, Any] = {"district_id": district_id}
    if edited_subject is not None:
        args["edited_subject"] = edited_subject
    if edited_body is not None:
        args["edited_body"] = edited_body
    return _dispatch("edit_draft", args, write=True)


@mcp.tool()
def journify_reject_draft(district_id: str, reason: str) -> Dict[str, Any]:
    """Reject a district's draft with a reason. Sets district.status=rejected."""
    return _dispatch("reject_draft", {"district_id": district_id, "reason": reason}, write=True)


@mcp.tool()
def journify_ingest_districts(records: Any) -> Dict[str, Any]:
    """Bulk-ingest districts. Accepts either {"target_districts": [...]} or a
    bare list. Idempotent on external_id. Returns ingest_ack with counts +
    skipped record reasons."""
    raw_records = _unwrap_district_payload(records)
    db: Session = SessionLocal()
    try:
        ack = _upsert_districts(raw_records, db)
        return ack.model_dump() if hasattr(ack, "model_dump") else dict(ack)
    finally:
        db.close()


@mcp.tool()
def journify_ingest_signals(records: Any) -> Dict[str, Any]:
    """Bulk-ingest signals. Accepts either {"signals": [...]} or a bare list.
    Auto-resolves each signal against existing districts. Returns counts +
    per-signal match strategy."""
    raw_records = _unwrap_signal_payload(records)
    db: Session = SessionLocal()
    try:
        ack = _upsert_signals(raw_records, db)
        return ack.model_dump() if hasattr(ack, "model_dump") else dict(ack)
    finally:
        db.close()


# ─── Entrypoint ────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server over stdio. This is what `journify-mcp` invokes."""
    mcp.run()


if __name__ == "__main__":
    main()
