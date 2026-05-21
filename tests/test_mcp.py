"""Smoke tests for the MCP server.

We don't spin up stdio transport — we exercise the in-process tool surface
directly via FastMCP's call_tool / list_tools APIs. SessionLocal is
monkeypatched to the test engine so we don't touch the dev DB.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy.orm import sessionmaker

from app import mcp_server
from app.api import ingest as ingest_api


def _patch_session(monkeypatch, db_engine) -> None:
    """Bind every `SessionLocal()` call inside mcp_server to the test DB."""
    TestSession = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(mcp_server, "SessionLocal", TestSession)


def _call(name: str, args: dict | None = None) -> dict:
    """Run an MCP tool via the official call_tool path and return the parsed result.

    FastMCP returns (content_blocks, structured_result), where structured_result
    wraps a non-pydantic dict return as {"result": {...}}. We unwrap that here so
    tests can assert against the underlying tool dict directly.
    """
    raw = asyncio.run(mcp_server.mcp.call_tool(name, args or {}))
    if isinstance(raw, tuple) and len(raw) == 2:
        _, structured = raw
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        if structured is not None:
            return structured
        text = raw[0][0].text if raw[0] else "{}"
        return json.loads(text)
    return raw  # type: ignore[return-value]


def test_lists_all_ten_tools():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "journify_list_districts",
        "journify_get_district",
        "journify_summarize_pipeline",
        "journify_list_unmatched_signals",
        "journify_run_pipeline",
        "journify_approve_draft",
        "journify_edit_draft",
        "journify_reject_draft",
        "journify_ingest_districts",
        "journify_ingest_signals",
    }
    assert expected.issubset(names), f"missing tools: {expected - names}"


def test_list_districts_empty(monkeypatch, db_engine):
    _patch_session(monkeypatch, db_engine)
    out = _call("journify_list_districts", {})
    assert out["count"] == 0
    assert out["districts"] == []


def test_ingest_then_list_then_get(monkeypatch, db_engine):
    _patch_session(monkeypatch, db_engine)

    payload = {
        "target_districts": [
            {"district_id": "D023", "name": "Grandview ISD", "state": "TX",
             "website": "grandviewisd.org", "enrollment": 7400,
             "intake_notes": "Webinar attendee 4/10"},
            {"district_id": "D006", "name": "Pinecrest Academy", "state": "FL",
             "enrollment": 320},
        ]
    }
    ack = _call("journify_ingest_districts", {"records": payload})
    assert ack["accepted"] >= 2

    listing = _call("journify_list_districts", {"state": "TX"})
    assert listing["count"] == 1
    assert listing["districts"][0]["district_id"] == "D023"

    detail = _call("journify_get_district", {"district_id": "D023"})
    assert detail["district_id"] == "D023"
    assert detail["enrollment"] == 7400
    assert detail["enrichment"] is None  # no pipeline run yet


def test_unmatched_signals_surface(monkeypatch, db_engine):
    _patch_session(monkeypatch, db_engine)

    # Ingest a district + a signal that won't match (only IP, no district hint)
    _call("journify_ingest_districts", {"records": {"target_districts": [
        {"district_id": "D023", "name": "Grandview ISD", "state": "TX"}
    ]}})
    _call("journify_ingest_signals", {"records": {"signals": [
        {"signal_id": "SIG_ORPHAN", "type": "content_view", "date": "2026-04-01",
         "ip_address": "10.0.0.1", "page_path": "/pricing"}
    ]}})

    out = _call("journify_list_unmatched_signals", {})
    assert out["count"] == 1
    assert out["signals"][0]["signal_id"] == "SIG_ORPHAN"


def test_summarize_pipeline_returns_counts(monkeypatch, db_engine):
    _patch_session(monkeypatch, db_engine)

    _call("journify_ingest_districts", {"records": {"target_districts": [
        {"district_id": "D023", "name": "Grandview ISD", "state": "TX", "enrollment": 7400},
        {"district_id": "D006", "name": "Pinecrest Academy", "state": "FL", "enrollment": 320},
    ]}})

    out = _call("journify_summarize_pipeline", {})
    assert out["total_districts"] == 2
    assert "by_status" in out
    assert "signals_total" in out


def test_reject_draft_without_district_returns_error(monkeypatch, db_engine):
    _patch_session(monkeypatch, db_engine)
    out = _call("journify_reject_draft", {"district_id": "D999", "reason": "test"})
    # write tools auto-inject user_confirmed=True; the chat_tools dispatch should
    # return an error dict for missing district rather than raise.
    assert "error" in out
