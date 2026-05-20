"""Claude-powered chat copilot for SDRs.

Loop:
  1. Send conversation + tool schemas to Claude.
  2. If the response includes a tool_use block, execute it server-side
     against the live DB.
  3. Feed the tool_result back into the conversation; repeat until Claude
     stops calling tools.
  4. Return the final assistant text + a tool-call trace the UI renders
     as small status pills.

Confirmation policy: write tools require `user_confirmed=true`. The
system prompt instructs Claude to always ask the user in conversation
before passing that flag. If it tries to call a write tool with
user_confirmed=false, the executor returns a "needs_confirmation" error
that Claude then surfaces to the user as a confirmation prompt.

The static system prompt + tool defs are cached (cache_control: ephemeral)
so multi-turn chats only pay for the new turn each round.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.services.chat_tools import TOOL_SCHEMAS, WRITE_TOOLS, execute_tool


MODEL = os.getenv("JOURNIFY_CHAT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 1500
MAX_TURNS = 8  # safety cap on tool-use loop iterations


_SYSTEM = """You are the Journify Outreach Copilot — an in-app assistant
for an SDR working a list of K-12 school districts.

Journify Learning is "The AI Assistant for Special Education." It
automates SPED paperwork (IEP drafting, present-levels summaries, parent
updates), tracks IEP goal progress, and generates standards-aligned
instructional materials. Public outcomes: teachers report 4+ hours back
per day, up to 50% time savings, IEP materials in under 5 minutes.
ESSA Tier 4 + Responsibly Designed AI certified. Operating in 17 states
with 10,000+ students served. INTEGRATES with existing IEP systems
(SEIS, Frontline) — does not replace them.

Your job: help the SDR analyze the pipeline, answer specific questions
about districts and signals, and (when explicitly asked) perform actions
on their behalf — but always with confirmation.

## Tools

You have read tools (list/get/summarize) and write tools (run_pipeline,
approve_draft, edit_draft, reject_draft).

For read tools: call them freely whenever you need data. Don't ask permission
to look something up.

For WRITE tools you MUST follow this two-step protocol:
  1. Identify what the user wants to do.
  2. Reply describing exactly what you're about to do and ask the user to
     confirm — e.g. "I'll approve D023's draft (subject: 'IEP compliance at
     scale — for Grandview ISD'). Confirm?"
  3. Only on a clear YES from the user (yes/proceed/confirm/go ahead) do you
     call the write tool with user_confirmed=true.

Never call a write tool with user_confirmed=true if the user has not
explicitly confirmed in this conversation. If you do, the tool refuses and
returns an error.

## Style

- Concise. Default to 1-3 short sentences plus a small data table or list.
- Reference districts by name + ID, e.g. "Grandview ISD (D023)".
- When showing numbers, prefer Markdown tables for ≥3 rows, bullets for ≤3.
- Don't apologize. Don't restate the question.
- If the data shows nothing or the SDR is wrong about something, say so
  plainly and tell them what is true.

## Domain context

- fit_score is 0-100. ≥75 = strong fit, 50-74 = moderate, <50 = weak.
- Status lifecycle: pending → enriching → enriched → edited/approved/rejected.
  duplicate and non_fit are auto-archived.
- A "briefed" district has been through the enrichment LLM pass and has a
  draft email queued for SDR review.
"""


def chat(messages: List[Dict[str, Any]], db: Session) -> Dict[str, Any]:
    """Run one chat turn through the tool-use loop.

    `messages` is the full conversation in Anthropic format:
      [{role: "user"|"assistant", content: str | list of blocks}, ...]

    Returns:
      {
        reply: str,                  # final assistant text
        tool_calls: [{name, input, result, error?, needs_confirmation?}],
        messages: [...]              # updated conversation incl. tool blocks
      }
    """
    client = Anthropic()
    tool_calls: List[Dict[str, Any]] = []
    working_messages = list(messages)

    for _ in range(MAX_TURNS):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_SCHEMAS,
            messages=working_messages,
        )

        # Capture the assistant message verbatim so we can feed tool_results
        # back in the next turn if needed.
        assistant_content = [_block_to_dict(b) for b in resp.content]
        working_messages.append({"role": "assistant", "content": assistant_content})

        if resp.stop_reason != "tool_use":
            return {
                "reply": _extract_text(resp.content),
                "tool_calls": tool_calls,
                "messages": working_messages,
            }

        # Execute any tool_use blocks; build user message with tool_results.
        tool_results_blocks: List[Dict[str, Any]] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name = block.name
            args = block.input or {}
            result = execute_tool(name, args, db)
            tool_calls.append({
                "name": name,
                "input": args,
                "result": result,
                "needs_confirmation": result.get("needs_confirmation", False) if isinstance(result, dict) else False,
                "is_write": name in WRITE_TOOLS,
            })
            tool_results_blocks.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": _stringify(result),
            })

        working_messages.append({"role": "user", "content": tool_results_blocks})

    # Loop budget exhausted — return whatever we have.
    return {
        "reply": "(I hit my tool-loop budget without finishing — try rephrasing?)",
        "tool_calls": tool_calls,
        "messages": working_messages,
    }


def _block_to_dict(b: Any) -> Dict[str, Any]:
    t = getattr(b, "type", None)
    if t == "text":
        return {"type": "text", "text": b.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    # Unknown block — fall back to model_dump if available
    if hasattr(b, "model_dump"):
        return b.model_dump()
    return {"type": t or "unknown"}


def _extract_text(content: List[Any]) -> str:
    parts: List[str] = []
    for b in content:
        if getattr(b, "type", None) == "text":
            parts.append(b.text)
    return "\n\n".join(parts).strip() or "(no text response)"


def _stringify(result: Any) -> str:
    import json
    try:
        return json.dumps(result, default=str)
    except Exception:
        return str(result)
