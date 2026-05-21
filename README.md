# District Outreach Copilot

> Journify take-home — Riaan Kumar, May 2026.

A small system that turns a weekly drop of target K-12 districts plus intent
signals into a queue of researched, ranked, send-ready outreach emails. Every
non-boilerplate claim cites the evidence behind it. The SDR stays in the loop
on every send.

## The problem (from the brief)

A founding-team SDR gets ~50 districts a week. Today she opens four tabs per
district — NCES, the district site, LinkedIn, Google News — researches for
15–30 minutes, picks a decision-maker, and writes a first-touch email that
doesn't read like spam. 50 × 20 minutes = ~16 hours of weekly busywork before
a single send.

This copilot collapses the research-and-draft pass to ~10–15s per district,
then hands the SDR an approval queue. She edits, approves, or rejects. She
never loses sight of *why* a claim or hook is in the email.

## What it does

1. **Ingest** districts + signals over HTTP (bulk POST, idempotent upserts).
2. **Resolve** each signal to a district via a cascade — direct ID → email
   domain → fuzzy name + state. Strategy, confidence, and notes persist per
   signal.
3. **Sweep** the list for duplicates (normalized-name + state collisions) and
   non-fits (private academies, microschools, sub-1k enrollment).
4. **Enrich** each viable district via Claude with a typed tool call —
   SPED footprint, decision-makers, region context, fit score (0–100) with
   per-criterion breakdown, full citation audit trail.
5. **Draft** a first-touch email grounded in the district's actual signals.
   Every claim and hook cites its source.
6. **Approve / edit / reject** in the UI. State propagates back through the
   district lifecycle and into the pipeline view.

## The two views (must-have, per brief)

**Approval queue** (`/`) — Clay-style spreadsheet of districts with status
pills, named views (*Today's queue*, *By state*, *Hot signals*), and inline
preview. Click any row for the detail panel: full enrichment, fit-score
breakdown with citations, draft email with citation chips, three buttons.
Edit inline, approve, or reject.

**Pipeline view** (`/pipeline`) — Approved districts segmented by send
priority (hot signals < 7 days old, warm enrichment-strong, cold backfill).
This is the *"who do I send to today"* view. Sortable by score, signal
recency, or state.

## Edge cases the brief asked for

| Case | How it's handled | Where |
|---|---|---|
| Near-duplicate names (`Brookhaven Public Schools` vs. `Brookhaven Public Sch.`) | Normalized name + state collision → marked `duplicate_of_external_id` | `app/services/edge_cases.py` |
| Non-fit records (private, microschool, <1k students) | Marked `non_fit` with `non_fit_reason`, hidden from main queue | `edge_cases.py` |
| Signals that don't match any district | Surfaced on `/unmatched` with manual-match + add-district actions | `app/api/ingest.py`, `UnmatchedPage.tsx` |
| Sparse public data | Enrichment marks low-confidence fields, fit-score adjusts down with explicit note | `enrichment.py` |
| Inconsistent name formatting | Suffix-stripping normalizer (Public Schools → PS, School District → SD, ISD, etc.) before fuzzy match | `app/services/normalize.py` (unit-tested) |

## Architecture note

```
Marketing list  ─►  POST /api/districts/bulk ┐
                                              ├─►  signal_matcher  ─►  edge_cases.sweep
Signal webhooks ─►  POST /api/signals/bulk   ┘                                │
                                                                              ▼
                            ┌─────────────────────────────────────────────────┐
                            │  per-district pipeline                          │
                            │  enrichment.py → fit_score → email_drafter      │
                            │  (Claude tool-use, cached system prompt)        │
                            └─────────────────────────────────────────────────┘
                                                                              │
                                          citations attached at every step    │
                                                                              ▼
                                                        SDR UI: approve / edit / reject
                                                                              │
                                                                              ▼
                                                        pipeline view (segmented)
```

**Stack:** FastAPI + SQLAlchemy + SQLite *or* Postgres (backend, driver
selected by `DATABASE_URL`), Vite + React + TypeScript (frontend),
Anthropic Claude Sonnet 4.6 for enrichment and drafting. `uv` for Python,
`npm` for the frontend.

**Why Claude over OpenAI:** Tool-use returns typed structured output with no
JSON repair loops; a schema change is one TypedDict edit, not a parser
rewrite. System-prompt caching cuts cost ~4–10× across a 30-district batch.

**Why SQLite by default, Postgres by env var:** SQLite is right-sized for
local dev + the in-memory test fixture (147 tests run in <2s). Postgres is
what you set in prod. Switching is one env var: `DATABASE_URL=postgresql+psycopg2://...`.
The codebase was built on SQLite and then run end-to-end against Postgres 16
without a code change — that's the proof the SQLAlchemy abstraction held.
Citation polymorphism ports cleanly between both.

**Why FastAPI:** Typed routes + auto-Swagger at `/docs` mean the panel can
poke the API live during the demo without me screen-sharing a terminal.

**Why HTTP ingest instead of file-reads on startup:** The brief asks for the
upload boundary to look the way it would in prod — marketing list-builds and
third-party webhooks. `scripts/seed.py` POSTs the two sample JSON files
through the bulk endpoints. Same path the SDR's tools will use.

## Citations & grounding

Citations are a first-class table, not a string on the draft. Each citation
is polymorphic — it attaches to either an `enrichment` or an `email_draft`,
names the `field_name` it grounds (which specific claim), and tags its
`source_type`:

- `signal` — a row in the signals table, cited by ID
- `intake_note` — free-text note from the original ingest record
- `inference` — Claude extrapolating from facts (e.g. *"Texas + 12k students +
  south-central region → likely Region 13 ESC affiliated"*)

**Inference is allowed but tagged, and required to declare `confidence < 0.6`.**
The UI renders citation chips inline beside each claim and a full citation
list under the draft. Hovering a chip shows the source quote.

## What "good email" means here

Outbound quality is subjective, so I made it testable. `tests/test_email_style.py`
enforces:

- 3-paragraph body (hook → relevance → CTA)
- Subject line includes district name + an outcome word, not "[signal], on time"
- No em dashes (an AI tell), no banned AI vocabulary
  (`delve`, `robust`, `seamless`, `comprehensive`, ...)
- Hook must cite a `signal` or a high-confidence enrichment field — pure-inference
  hooks fail the test
- One CTA per email, never two

`tests/test_spec_requirements.py` walks the full ingest → enrich → draft path
and asserts every must-have from this brief is satisfied end-to-end. This is
a light eval, not a labeled-pair eval. See [Scope decisions](#scope-decisions)
for why I traded that.

## Nice-to-haves that shipped

- **SDR copilot chat** (`ChatDrawer.tsx`, `app/services/chat.py`) —
  Natural-language Q&A *and* actions with confirmation. Ask *"Which Texas
  districts have hot signals in the last 7 days?"* and get a table back. Ask
  *"Approve all hot Texas drafts"* and it stages the action and waits for
  confirmation. Typed tool-calls under the hood — no SQL injection surface.
- **Sourcing agent** (`SourcingPanel.tsx`, `app/services/sourcing.py`) — When
  the input list is short, the SDR can search-and-add new districts by
  criteria. A verification layer requires evidence before insert.
- **Light eval suite** — the email-style + spec-requirements tests above.

## Scope decisions

| In | Out | Why out |
|---|---|---|
| Bulk + single ingest endpoints | Auth | Take-home; one user; demo runs locally |
| Idempotent everywhere | Pagination | 30 districts; not needed yet |
| Per-claim citations | SSE for pipeline progress | UX is fine at ~10s; polling works |
| Edge-case sweep | Labeled-pair LLM evals | Above the time budget — light eval shipped instead |
| SDR copilot chat | 2nd-touch generator | First-touch quality matters more for an interview eval |
| Sourcing agent | Mock CRM-sync endpoint | One-line addition; happy to demo on request |
| Pipeline segmentation | Dedicated "explain this score" view | Fit-score already exposes per-criterion breakdown + citations |

A founding-team eval cares more about a tight first-touch loop than a
wide-but-shallow feature list. I optimized for the first.

## Quick start

```bash
# 1) install
uv sync
cd frontend && npm install && cd ..

# 2) configure
cp .env.example .env
# add your ANTHROPIC_API_KEY

# 3) run (two terminals)
uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev   # → http://localhost:5173

# 4) seed (POSTs target_districts.json + signals.json through the bulk endpoints —
#    matching the "over the wire" constraint in the brief)
uv run python scripts/seed.py
```

Open `http://localhost:5173`, click a `pending` district, hit **Run pipeline**.
Enrichment + email draft appear in ~10–15s.

### Running against Postgres instead of SQLite

```bash
# 1) install + start Postgres (macOS)
brew install postgresql@16
brew services start postgresql@16

# 2) create the DB
createdb journify_copilot

# 3) point the app at it (any of these works)
export DATABASE_URL='postgresql+psycopg2://riaankumar@localhost:5432/journify_copilot'
uv run uvicorn app.main:app --port 8000

# 4) re-seed
uv run python scripts/seed.py
```

That's it. `init_db()` creates the schema; the bulk-ingest endpoints work
identically. Same 147 tests pass on both backends (tests use in-memory
SQLite for speed).

## Demo path

- **D023 Grandview ISD** — direct_id match on a SPED webinar signal.
  Enrichment names James Peterson, Director of Special Education. Email draft
  addresses him with hooks tied to SIG001 + SIG023 + SIG029. Three citations
  on the hook line.
- **D011 Salt Lake County** — fuzzy match resolves SIG005 (LinkedIn) +
  SIG008 (RFP). Email subject references "RFP-2026-014" directly.
- **D009 Brookhaven Public Schools** — email-domain match on two whitepaper
  downloads (SIG002, SIG007). Decision-maker inferred from district website;
  tagged as `source_type=inference`, confidence 0.55.
- **D010 Brookhaven Public Sch.** — auto-marked `duplicate_of D009`. Hidden
  from the main queue.
- **D006 Pinecrest Academy** / **D007 St. Augustine Charter Collective** —
  auto-marked `non_fit` with reason (*"private academy"*, *"sub-1k enrollment"*).
- **Unmatched signals** — surface on `/unmatched` with manual-match + add-district actions.

## Endpoints

| Method | Path | Use |
|---|---|---|
| POST | `/api/districts/bulk` | Ingest districts; auto-runs edge-case sweep + re-resolves signals |
| POST | `/api/signals/bulk` | Ingest signals; auto-resolves |
| POST | `/api/signals/resolve` | Re-run matcher on unresolved signals |
| GET | `/api/signals/unmatched` | Signals that didn't match any district |
| GET | `/api/districts` | Dashboard list |
| GET | `/api/districts/{id}` | Detail with embedded enrichment + draft |
| GET | `/api/districts/{id}/citations` | Full citation list |
| GET | `/api/districts/{id}/signals` | Resolved signals for one district |
| POST | `/api/districts/{id}/run-pipeline` | resolve → enrich → draft |
| POST | `/api/districts/{id}/enrich` | Enrichment only |
| POST | `/api/districts/{id}/draft` | Draft only (requires enrichment) |
| PATCH | `/api/email-drafts/{id}` | `{action: approve\|edit\|reject, ...}` |
| POST | `/api/chat` | SDR copilot — Q&A + actions w/ confirmation |
| POST | `/api/sourcing/search` | Find candidate new districts by criteria |
| POST | `/api/sourcing/add` | Add a sourced district (with verification) |

`/docs` exposes the Swagger UI.

## Tests

```bash
uv run pytest tests/ -v
```

| File | Covers |
|---|---|
| `test_signal_matcher.py` | Direct-ID, email-domain, fuzzy-name resolution; confidence scoring |
| `test_edge_cases.py` | Duplicate detection, non-fit classification |
| `test_normalize.py` | Suffix-stripping + state extraction |
| `test_enrichment.py` | Tool-call schema, citation attachment |
| `test_email_style.py` | "Good email" definition (no em dashes, no AI slop, structure, hook grounding) |
| `test_spec_requirements.py` | End-to-end: every must-have from this brief |
| `test_pipeline_view.py` | Segmentation logic |
| `test_chat_tools.py` | Copilot tool dispatch + action confirmation gate |
| `test_sourcing.py` | Sourcing search + verification |
| `test_api.py` | Idempotency, bulk-ingest partial-failure handling |

## Accuracy eval (labeled-pair fit-score test)

The unit tests assert the *shape* of enrichment. This eval asserts its
*accuracy*. 24 districts are hand-labeled against the brief's 5-criterion
rubric (enrollment, intent, decision-maker, pain, public traction), with
reasoning per label. The script runs the pipeline on each, compares the
model's `fit_score` to the human label, and reports correlation + bucket
accuracy.

```bash
uv run python -m evals.run_eval
```

**Last run (against Postgres, Sonnet 4.6, 23 paired predictions):**

| Metric | Value | What it means |
|---|---|---|
| Spearman ρ | **0.913** | Very strong rank agreement — model orders districts almost identically to a human |
| Pearson r | **0.924** | Linear agreement on raw 0-100 scores |
| MAE | **8.39 points** | Average score is within ±8.4 points of the human label |
| Tier accuracy | **87.0%** | Strong/moderate/weak bucket match (all weak-tier correctly identified) |

Failure mode worth flagging: the model over-scores weak-signal districts
by 15-25 points on a handful of records (D029 Westbrook, D019 Whispering Pines),
pulling them up a bucket. Under-scores districts whose only intent signal is
a news mention (D017 Tucson Unified). Labels and per-district report live in
`evals/labels.jsonl` and `evals/last_run_report.md` — both overridable; rerun
the eval anytime with `uv run python -m evals.run_eval`.

## MCP server (call the copilot from Claude Code, Codex, Cursor)

The dashboard is also an MCP server. Any MCP-speaking coding tool can call
enrichment, listing, draft approval, and ingest as first-class tools — no
HTTP, no browser, no API key. The SDR's tools become an agent surface.

Surface (10 tools, all prefixed `journify_`):

| Tool | What it does |
|---|---|
| `journify_list_districts` | Filter by status / state / min_fit, sort by fit/enrollment/signals/name |
| `journify_get_district` | Full packet: enrichment + draft + citations + signals |
| `journify_summarize_pipeline` | Counts by status, by state, avg fit, signal coverage |
| `journify_list_unmatched_signals` | The manual-match queue |
| `journify_run_pipeline` | resolve → enrich → draft (write) |
| `journify_approve_draft` / `journify_edit_draft` / `journify_reject_draft` | Lifecycle actions |
| `journify_ingest_districts` / `journify_ingest_signals` | Bulk ingest, idempotent |

### Wiring it into Claude Code

Add to `~/.claude.json` (or your project's `.mcp.json`):

```json
{
  "mcpServers": {
    "journify": {
      "command": "uv",
      "args": [
        "--directory", "/Users/riaankumar/projects/journify-takehome",
        "run", "python", "-m", "app.mcp_server"
      ],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

Then from any Claude Code session:

```
> Enrich D023 and show me the draft
  → calls journify_run_pipeline, then journify_get_district
> Approve it
  → calls journify_approve_draft
> What's the pipeline look like for Texas?
  → calls journify_list_districts({state: "TX"})
```

The MCP host (Claude Code) gates every write tool — the user approves each
call before it runs. The in-process chat copilot keeps its own confirmation
gate because it has no host approval layer.

### Why this matters

It reframes the project from "an SDR dashboard" to "a typed agent surface
for K-12 outreach". Same code, different transport. The SDR can stay in
her existing AI workflow instead of context-switching to a separate UI.

## What I'd build next

- **Email-quality eval** — same labeled-pair pattern but on email outputs.
  Hand-grade 20 drafts on hook specificity, tone, CTA clarity. Fit-score
  eval already shipped (see Accuracy eval above).
- **Re-prompt on weak-signal over-scoring** — the eval surfaced a calibration
  miss (model over-scores districts with only LinkedIn engagement by ~20 points).
  Tightening the rubric in the system prompt should close that gap.
- **2nd-touch generator** with awareness of the approved 1st-touch and any
  reply received.
- **HubSpot-shaped export endpoint** (one-shot stub already mapped).
- **SSE for pipeline progress** so the SDR sees enrichment streaming in
  rather than waiting 10s with no feedback.
- **Alembic migrations** for the Postgres path (currently `Base.metadata.create_all`
  works because the schema is small and stable).
