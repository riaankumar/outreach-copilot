# District Outreach Copilot

Journify take-home — a copilot that turns a list of K-12 districts plus
intent signals into a queue of grounded outreach emails, each citing the
evidence behind every claim.

## What it does

1. **Ingest** districts + intent signals (bulk or single, idempotent upserts).
2. **Match** each signal to a district by (a) direct ID, (b) email domain,
   (c) fuzzy name. Confidence + strategy + notes are persisted per signal.
3. **Sweep** edge cases — duplicates by normalized name + state, non-fit
   orgs (private academies, microschools, sub-1k enrollment).
4. **Enrich** each viable district via Claude with a structured tool call:
   SPED footprint, decision-makers, region context, fit score (0–100) with
   per-criterion breakdown, and an audit trail of citations.
5. **Draft** a first-touch outreach email grounded in the district's actual
   signals — every non-boilerplate claim cites its source.
6. **Approve / edit / reject** via the UI; status propagates back to the
   district lifecycle.

## Stack

- **Backend**: FastAPI + SQLAlchemy + SQLite, Anthropic Python SDK (tool-use for
  structured output, system-prompt caching for cost reduction).
- **Frontend**: Vite + React + TypeScript. Single-page dashboard, click any
  card for the full enrichment + draft modal with citation chips.
- **Pkg mgmt**: `uv` for Python, `npm` for the frontend.

## Quick start

```bash
# 1) install
uv sync
cd frontend && npm install && cd ..

# 2) configure
cp .env.example .env
# put your ANTHROPIC_API_KEY in .env

# 3) run (two terminals)
uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev   # → http://localhost:5173

# 4) seed (samples live in ~/Downloads or repo data/)
uv run python scripts/seed.py
```

Open `http://localhost:5173`, click a `pending` district, hit **Run pipeline**.
Enrichment + email draft appear in ~10–15s, citations and all.

## Demo path

- **D023 Grandview ISD** — direct_id match on a SPED webinar signal →
  enrichment names "James Peterson, Director of Special Education" →
  email draft addressed to him with hooks tied to SIG001 + SIG023 + SIG029.
- **D011 Salt Lake County** — fuzzy match resolves SIG005 (LinkedIn) +
  SIG008 (RFP). Email subject references "RFP-2026-014".
- **D009 Brookhaven Public Schools** — email-domain match on two whitepaper
  downloads (SIG002, SIG007).
- **D010 Brookhaven Public Sch.** — automatically marked `duplicate_of D009`.
- **D006 Pinecrest Academy** / **D007 St. Augustine Charter Collective** —
  automatically marked `non_fit` with reason.

## Endpoints

| Method | Path | Use |
|---|---|---|
| POST | `/api/districts/bulk` | Ingest districts (also runs edge_case sweep + re-resolves signals) |
| POST | `/api/signals/bulk` | Ingest signals (auto-resolves) |
| POST | `/api/signals/resolve` | Re-run matcher on unresolved signals |
| GET | `/api/districts` | List dashboard |
| GET | `/api/districts/{id}` | Detail with embedded enrichment + draft |
| GET | `/api/districts/{id}/signals` | Resolved signals for one district |
| POST | `/api/districts/{id}/run-pipeline` | resolve → enrich → draft |
| POST | `/api/districts/{id}/enrich` | Just enrichment |
| POST | `/api/districts/{id}/draft` | Just email draft (requires enrichment) |
| PATCH | `/api/email-drafts/{id}` | `{action: approve|edit|reject, ...}` |

`/docs` exposes the FastAPI auto-generated Swagger UI.

## Design choices worth flagging

- **Citations are first-class.** A separate table polymorphically attaches
  to enrichments OR email_drafts, with `field_name` (which claim) +
  `source_type` ∈ {signal, intake_note, inference}. The UI renders inline
  chips and a full citation list under the draft.
- **Inference is allowed but tagged.** Claude can extrapolate (e.g. region
  context from state + enrollment), but `source_type='inference'` claims
  are required to declare `confidence < 0.6`.
- **Tool use for structured output.** Both enrichment and drafting force
  Claude to call a typed tool — no ad-hoc JSON parsing or repair loops.
- **System-prompt caching.** Both LLM calls use `cache_control: ephemeral`
  on the static system prompt — across 25+ districts that's a 4–10× cost cut.
- **Best-effort batch ingest.** A single bad record doesn't reject the
  batch — skipped records are reported in the ack. Matches real
  marketing-list ingest.
- **Idempotent everywhere.** Re-running ingest, resolve, or pipeline
  converges on the same state. Safe to demo twice.

## Tests

```bash
uv run pytest tests/ -v
```

Only the suffix-stripping normalizer has unit tests — everything else is
exercised by the end-to-end seed → pipeline flow.

## Out of scope

Auth, pagination, real LLM evals, SSE for pipeline progress, swapping
SQLite for Postgres. All deliberately punted for take-home scope.
