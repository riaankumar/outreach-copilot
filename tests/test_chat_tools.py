"""Chat tool executors — read tools work, write tools refuse without confirmation.

These cover the tool layer in isolation; the LLM loop itself is exercised
once with a mocked Anthropic client in test_chat_loop below.
"""
from __future__ import annotations

import pytest

from app.services import chat_tools
from app.services.chat_tools import execute_tool
from tests.conftest import SAMPLE_DISTRICTS, SAMPLE_SIGNALS


@pytest.fixture
def seeded(client):
    """Seed via the API so we exercise the same ingest path the app uses."""
    client.post("/api/districts/bulk", json=SAMPLE_DISTRICTS)
    client.post("/api/signals/bulk", json={"signals": SAMPLE_SIGNALS})


# ─── Read tools ────────────────────────────────────────────

def test_list_districts_filters_by_status(seeded, db):
    out = execute_tool("list_districts", {"status": "pending"}, db)
    assert out["count"] >= 1
    assert all(d["status"] == "pending" for d in out["districts"])


def test_list_districts_filters_by_state(seeded, db):
    out = execute_tool("list_districts", {"state": "MA"}, db)
    assert out["count"] >= 1
    assert all(d["state"] == "MA" for d in out["districts"])


def test_list_districts_sorts_by_name(seeded, db):
    out = execute_tool("list_districts", {"sort_by": "name", "limit": 50}, db)
    names = [d["name"] for d in out["districts"]]
    assert names == sorted(names)


def test_get_district_returns_full_record(seeded, db):
    out = execute_tool("get_district", {"district_id": "D023"}, db)
    assert out["district_id"] == "D023"
    assert out["name"] == "Grandview ISD"
    # Auto-resolve put at least one signal on D023
    assert any(s["signal_id"] == "SIG001" for s in out["signals"])


def test_get_district_404(seeded, db):
    out = execute_tool("get_district", {"district_id": "NOPE"}, db)
    assert "error" in out


def test_summarize_pipeline_returns_shape(seeded, db):
    out = execute_tool("summarize_pipeline", {}, db)
    assert "total_districts" in out
    assert "by_status" in out
    assert "by_state_top" in out
    assert out["signals_total"] == len(SAMPLE_SIGNALS)


# ─── Write tools require user_confirmed ─────────────────────

def test_run_pipeline_refuses_without_confirmation(seeded, db):
    out = execute_tool("run_pipeline", {"district_id": "D023"}, db)
    assert out.get("needs_confirmation") is True
    assert "user_confirmed=true is required" in out["message"]


def test_approve_refuses_without_confirmation(seeded, db):
    out = execute_tool("approve_draft", {"district_id": "D023"}, db)
    assert out.get("needs_confirmation") is True


def test_edit_refuses_without_confirmation(seeded, db):
    out = execute_tool("edit_draft", {"district_id": "D023", "edited_subject": "x"}, db)
    assert out.get("needs_confirmation") is True


def test_reject_refuses_without_confirmation(seeded, db):
    out = execute_tool("reject_draft", {"district_id": "D023", "reason": "x"}, db)
    assert out.get("needs_confirmation") is True


def test_approve_executes_with_confirmation(seeded, db, monkeypatch):
    # Need an existing draft → run the pipeline first (with mocked LLMs)
    from app.services import enrichment, email_drafter
    from tests.test_enrichment import _fake_enrichment_response, _fake_draft_response
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    monkeypatch.setattr(email_drafter, "_call_claude", _fake_draft_response)

    enrichment.enrich_district("D023", db)
    email_drafter.draft_email("D023", db)

    out = execute_tool("approve_draft", {"district_id": "D023", "user_confirmed": True}, db)
    assert out["ok"] is True
    assert out["district_status"] == "approved"


def test_edit_executes_with_confirmation(seeded, db, monkeypatch):
    from app.services import enrichment, email_drafter
    from tests.test_enrichment import _fake_enrichment_response, _fake_draft_response
    monkeypatch.setattr(enrichment, "_call_claude", _fake_enrichment_response)
    monkeypatch.setattr(email_drafter, "_call_claude", _fake_draft_response)

    enrichment.enrich_district("D023", db)
    email_drafter.draft_email("D023", db)

    out = execute_tool("edit_draft", {
        "district_id": "D023",
        "edited_subject": "From the chatbot",
        "user_confirmed": True,
    }, db)
    assert out["ok"] is True
    assert out["edited_subject"] == "From the chatbot"


def test_unknown_tool_returns_error(seeded, db):
    out = execute_tool("delete_everything", {}, db)
    assert "error" in out
    assert "unknown tool" in out["error"]


# ─── Chat loop with mocked Anthropic ───────────────────────

def test_chat_loop_executes_read_tool(seeded, db, monkeypatch):
    """End-to-end: mocked Claude calls list_districts; loop returns final text."""
    from app.services import chat as chat_service

    class _MockBlock:
        def __init__(self, type, **kw):
            self.type = type
            for k, v in kw.items():
                setattr(self, k, v)

    class _MockResp:
        def __init__(self, content, stop_reason="end_turn"):
            self.content = content
            self.stop_reason = stop_reason

    # Two-step mock: first turn returns a tool_use, second returns final text
    calls = {"n": 0}

    class _MockMessages:
        def create(self, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _MockResp([
                    _MockBlock("tool_use", id="t1", name="list_districts", input={"status": "pending", "limit": 3}),
                ], stop_reason="tool_use")
            return _MockResp([
                _MockBlock("text", text="You have a few pending districts — top ones listed above."),
            ], stop_reason="end_turn")

    class _MockClient:
        messages = _MockMessages()

    monkeypatch.setattr(chat_service, "Anthropic", lambda: _MockClient())

    out = chat_service.chat(
        [{"role": "user", "content": "What's pending?"}],
        db,
    )
    assert "pending" in out["reply"]
    assert len(out["tool_calls"]) == 1
    assert out["tool_calls"][0]["name"] == "list_districts"
    assert out["tool_calls"][0]["is_write"] is False


def test_chat_loop_write_blocked_without_confirmation(seeded, db, monkeypatch):
    """If Claude prematurely calls a write tool with user_confirmed=false,
    the executor surfaces a needs_confirmation error to the model."""
    from app.services import chat as chat_service

    class _MockBlock:
        def __init__(self, type, **kw):
            self.type = type
            for k, v in kw.items():
                setattr(self, k, v)

    class _MockResp:
        def __init__(self, content, stop_reason="end_turn"):
            self.content = content
            self.stop_reason = stop_reason

    calls = {"n": 0}

    class _MockMessages:
        def create(self, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _MockResp([
                    _MockBlock("tool_use", id="t1", name="approve_draft",
                               input={"district_id": "D023", "user_confirmed": False}),
                ], stop_reason="tool_use")
            return _MockResp([
                _MockBlock("text", text="I need you to confirm first — should I approve D023?"),
            ], stop_reason="end_turn")

    class _MockClient:
        messages = _MockMessages()

    monkeypatch.setattr(chat_service, "Anthropic", lambda: _MockClient())

    out = chat_service.chat(
        [{"role": "user", "content": "Approve D023"}],
        db,
    )
    # The write was attempted but blocked
    assert out["tool_calls"][0]["name"] == "approve_draft"
    assert out["tool_calls"][0]["is_write"] is True
    assert out["tool_calls"][0]["needs_confirmation"] is True
    # The DB should NOT have been changed
    d = execute_tool("get_district", {"district_id": "D023"}, db)
    assert d["status"] != "approved"
