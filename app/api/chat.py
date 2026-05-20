"""Chat endpoint for the SDR copilot."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models import get_session
from app.services import chat as chat_service

router = APIRouter()


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: Any  # str or list of blocks (tool_use / tool_result / text)


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(default_factory=list)


class ToolCallTrace(BaseModel):
    name: str
    input: Dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    needs_confirmation: bool = False
    is_write: bool = False


class ChatResponse(BaseModel):
    reply: str
    tool_calls: List[ToolCallTrace] = Field(default_factory=list)
    messages: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
def chat_turn(req: ChatRequest, db: Session = Depends(get_session)) -> ChatResponse:
    messages = [m.model_dump() for m in req.messages]
    out = chat_service.chat(messages, db)
    return ChatResponse(
        reply=out["reply"],
        tool_calls=[ToolCallTrace(**t) for t in out["tool_calls"]],
        messages=out["messages"],
    )
