"""FastAPI entry point.

Mounts ingest + list routers, initializes the SQLite schema on startup,
and opens CORS so the Vite dev server (port 5173) can call the API
directly during development.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import init_db
from app.api import ingest, districts, pipeline, chat, sourcing


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="District Outreach Copilot",
    version="0.1.0",
    lifespan=lifespan,
)

_default_origins = "http://localhost:5173,http://127.0.0.1:5173"
_allowed = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router, prefix="/api")
app.include_router(districts.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(sourcing.router, prefix="/api")


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
