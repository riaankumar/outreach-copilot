import { useEffect, useMemo, useState, type ReactNode } from 'react'
import ChatDrawer from './ChatDrawer'
import DistrictDetail from './DistrictDetail'
import type { District } from './types'
import './App.css'

type StatusFilter = 'all' | 'pending' | 'queue' | 'pipeline' | 'sent' | 'archived'
type SortKey = 'fit' | 'signals' | 'enrollment' | 'name'

const FILTER_SUBHEADINGS: Record<StatusFilter, { title: string; sub: string } | null> = {
  all: null,
  pending: { title: 'Pending', sub: 'Districts not yet briefed by the AI. Click to run the pipeline.' },
  queue: { title: 'Approval queue', sub: "AI-proposed packets waiting for your review. Approve, edit, or reject." },
  pipeline: { title: 'Pipeline', sub: 'Approved districts grouped by send priority. Top is what to send today.' },
  sent: { title: 'Sent', sub: 'Drafts you have already sent. For audit and reference.' },
  archived: { title: 'Archived', sub: 'Duplicates, non-fits, and rejected drafts.' },
}

function fmtEnrollment(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`
  return n.toLocaleString()
}

function statusLabel(s: string): string {
  return s.replace(/_/g, ' ')
}

function inFilter(d: District, f: StatusFilter): boolean {
  if (f === 'all') return true
  if (f === 'pending') return d.status === 'pending' || d.status === 'enriching'
  if (f === 'queue') return d.status === 'enriched' || d.status === 'edited'
  if (f === 'pipeline') return d.status === 'approved'
  if (f === 'sent') return d.status === 'sent'
  if (f === 'archived') return d.status === 'non_fit' || d.status === 'duplicate' || d.status === 'rejected'
  return true
}

function fitClass(score: number | null | undefined): string {
  if (score == null) return ''
  if (score >= 75) return 'fit-good'
  if (score >= 50) return 'fit-mid'
  return ''
}

export default function App() {
  const [districts, setDistricts] = useState<District[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [openId, setOpenId] = useState<string | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [filter, setFilter] = useState<StatusFilter>('all')
  const [sortKey, setSortKey] = useState<SortKey>('fit')
  const [query, setQuery] = useState('')

  async function refresh() {
    setLoading(true)
    try {
      const res = await fetch('/api/districts')
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      setDistricts(await res.json())
      setError(null)
    } catch (e: any) {
      setError(e.message ?? String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const counts = useMemo(() => {
    const c = { all: districts.length, pending: 0, queue: 0, pipeline: 0, sent: 0, archived: 0 }
    for (const d of districts) {
      if (d.status === 'pending' || d.status === 'enriching') c.pending++
      if (d.status === 'enriched' || d.status === 'edited') c.queue++
      if (d.status === 'approved') c.pipeline++
      if (d.status === 'sent') c.sent++
      if (d.status === 'non_fit' || d.status === 'duplicate' || d.status === 'rejected') c.archived++
    }
    return c
  }, [districts])

  const filteredSorted = useMemo(() => {
    const q = query.trim().toLowerCase()
    let rows = districts.filter((d) => inFilter(d, filter))
    if (q) {
      rows = rows.filter((d) =>
        d.name.toLowerCase().includes(q) ||
        (d.state ?? '').toLowerCase().includes(q) ||
        d.district_id.toLowerCase().includes(q),
      )
    }
    const compare: Record<SortKey, (a: District, b: District) => number> = {
      fit: (a, b) => (b.enrichment?.fit_score ?? -1) - (a.enrichment?.fit_score ?? -1),
      signals: (a, b) => b.signal_count - a.signal_count,
      enrollment: (a, b) => (b.enrollment ?? 0) - (a.enrollment ?? 0),
      name: (a, b) => a.name.localeCompare(b.name),
    }
    return [...rows].sort(compare[sortKey])
  }, [districts, filter, sortKey, query])

  // First pending district — used as the target for the "Brief next" quick action
  const nextPending = useMemo(
    () => districts.find((d) => d.status === 'pending'),
    [districts],
  )
  const [showQuickActions, setShowQuickActions] = useState(true)
  const [briefing, setBriefing] = useState(false)

  async function briefNext() {
    if (!nextPending) return
    setBriefing(true)
    try {
      await fetch(`/api/districts/${nextPending.district_id}/run-pipeline`, { method: 'POST' })
      await refresh()
      setOpenId(nextPending.district_id)
    } finally {
      setBriefing(false)
    }
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="dot" aria-hidden="true" />
            Journify <small>Outreach Copilot</small>
          </div>
          <div className="topbar-center">
            <div className="topbar-meta">
              <span><strong>{counts.all}</strong> districts</span>
              <span className="sep" />
              <span><strong>{counts.queue}</strong> in queue</span>
              <span className="sep" />
              <span><strong>{counts.pipeline}</strong> in pipeline</span>
              <span className="sep" />
              <span><strong>{counts.sent}</strong> sent</span>
            </div>
          </div>
          <div className="topbar-right">
            <UnmatchedSignalsButton />
            <button className="user-pill" aria-label="Account">
              <span className="avatar">RK</span>
              <span className="who">
                <span className="name">Riaan Kumar</span>
                <span className="org">Journify</span>
              </span>
            </button>
          </div>
        </div>
      </header>

      <main className="app">
        <div className="welcome">
          <div>
            <h2>Hey Riaan, ready to send today?</h2>
            <p className="lede">
              {counts.queue > 0
                ? `${counts.queue} packet${counts.queue === 1 ? '' : 's'} waiting for your review and ${counts.pipeline} approved ${counts.pipeline === 1 ? 'is' : 'are'} in the send queue.`
                : counts.pending > 0
                  ? `${counts.pending} pending district${counts.pending === 1 ? '' : 's'} ready to brief.`
                  : 'Inbox zero. Nice.'}
            </p>
          </div>
          <button className="quick-toggle" onClick={() => setShowQuickActions((v) => !v)}>
            {showQuickActions ? 'Hide' : 'Show'} quick actions
            <ChevronIcon flipped={!showQuickActions} />
          </button>
        </div>

        {showQuickActions && (
          <div className="quick-actions">
            <QuickCard
              icon="🚀" iconClass="brief"
              title={nextPending ? `Brief ${nextPending.name}` : 'Brief next district'}
              sub={nextPending ? `Run the AI pipeline on the next pending district (${nextPending.district_id}).` : 'No pending districts left.'}
              count={briefing ? '…' : undefined}
              onClick={briefNext}
              disabled={!nextPending || briefing}
            />
            <QuickCard
              icon="📥" iconClass="queue"
              title="Review the queue"
              sub="Open the approval queue and review pending packets."
              count={counts.queue || undefined}
              onClick={() => setFilter('queue')}
            />
            <QuickCard
              icon="📨" iconClass="send"
              title="Today's send list"
              sub="See approved drafts grouped by send priority."
              count={counts.pipeline || undefined}
              onClick={() => setFilter('pipeline')}
            />
            <QuickCard
              icon="✦" iconClass="chat"
              title="Ask the copilot"
              sub="Get a pipeline summary or run actions from chat."
              onClick={() => setChatOpen(true)}
            />
          </div>
        )}

        <div className="page-hdr">
          <div className="page-hdr-left">
            <span className="page-hdr-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
                <polyline points="9 22 9 12 15 12 15 22"/>
              </svg>
            </span>
            <div>
              <h1>Districts</h1>
              <p className="lede">
                Review AI-briefed districts, edit the dispatch draft if needed, and approve outreach.
              </p>
            </div>
          </div>
          <div className="page-hdr-actions">
            <button onClick={() => setFilter('queue')} aria-label="Open approval queue">
              <InboxIcon /> Inbox {counts.queue > 0 && <span className="cnt-badge">{counts.queue}</span>}
            </button>
            <button onClick={() => setChatOpen(true)} aria-label="Open analytics via copilot">
              <BarsIcon /> Analytics
            </button>
            <button onClick={refresh} disabled={loading} className="ghost" aria-label="Refresh">
              <RefreshIcon spinning={loading} />
            </button>
            <button
              className="primary"
              onClick={briefNext}
              disabled={!nextPending || briefing}
            >
              <PlusIcon /> Brief next
            </button>
          </div>
        </div>

        <div className="toolbar">
          <div className="filter-group" role="tablist" aria-label="Filter by status">
            <FilterBtn label="All" count={counts.all} active={filter === 'all'} onClick={() => setFilter('all')} />
            <FilterBtn label="Pending" count={counts.pending} active={filter === 'pending'} onClick={() => setFilter('pending')} />
            <FilterBtn label="Approval queue" count={counts.queue} active={filter === 'queue'} onClick={() => setFilter('queue')} />
            <FilterBtn label="Pipeline" count={counts.pipeline} active={filter === 'pipeline'} onClick={() => setFilter('pipeline')} />
            <FilterBtn label="Sent" count={counts.sent} active={filter === 'sent'} onClick={() => setFilter('sent')} />
            <FilterBtn label="Archived" count={counts.archived} active={filter === 'archived'} onClick={() => setFilter('archived')} />
          </div>

          <div className="search">
            <input
              type="text"
              placeholder="Search districts…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search districts"
            />
          </div>

          <div className="spacer" />

          <div className="sort">
            <span>Sort</span>
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              aria-label="Sort"
            >
              <option value="fit">Fit score</option>
              <option value="signals">Signal count</option>
              <option value="enrollment">Enrollment</option>
              <option value="name">Name (A–Z)</option>
            </select>
          </div>
        </div>

        {FILTER_SUBHEADINGS[filter] && (
          <div className="view-sub">
            <strong>{FILTER_SUBHEADINGS[filter]!.title}</strong>
            <span>{FILTER_SUBHEADINGS[filter]!.sub}</span>
          </div>
        )}

        {error && <div className="err">{error}</div>}

        {!error && filteredSorted.length === 0 && !loading && (
          <div className="empty">
            <h3>No districts match this view</h3>
            {districts.length === 0 ? (
              <p>Seed the database: <code>uv run python scripts/seed.py</code></p>
            ) : (
              <p>Try clearing the search or switching the filter.</p>
            )}
          </div>
        )}

        {filter === 'pipeline' && filteredSorted.length > 0 ? (
          <PipelineGroupedView
            districts={filteredSorted}
            openId={openId}
            onOpen={setOpenId}
          />
        ) : (
          <div className="grid">
            {filteredSorted.map((d, i) => (
              <DistrictCard
                key={d.district_id}
                d={d}
                index={i}
                selected={openId === d.district_id}
                showDraftPreview={filter === 'queue'}
                onClick={() => setOpenId(d.district_id)}
              />
            ))}
          </div>
        )}

        <footer className="ftr">
          <span>{filteredSorted.length} of {districts.length} shown</span>
          <span>API :8000 · UI :5173</span>
        </footer>
      </main>

      {openId && (
        <DistrictDetail
          districtId={openId}
          onClose={() => setOpenId(null)}
          onChanged={refresh}
        />
      )}

      <button
        className="copilot-fab"
        onClick={() => { setOpenId(null); setChatOpen(true) }}
        aria-label="Open SDR copilot"
      >
        <span className="copilot-fab-icon" aria-hidden="true">✦</span>
        Ask copilot
      </button>

      <ChatDrawer
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        onActionTaken={refresh}
      />
    </>
  )
}

function FilterBtn({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      role="tab"
      aria-selected={active}
      className={`filter-btn ${active ? 'on' : ''}`}
      onClick={onClick}
    >
      {label} <span className="cnt">{count}</span>
    </button>
  )
}

function DistrictCard({ d, index, selected, onClick, showDraftPreview = false }: {
  d: District; index: number; selected: boolean; onClick: () => void; showDraftPreview?: boolean
}) {
  const draft = d.email_draft
  const subject = draft ? (draft.edited_subject ?? draft.subject) : null
  const body = draft ? (draft.edited_body ?? draft.body) : null
  const bodyPreview = body ? body.split('\n').find((l) => l.trim())?.slice(0, 140) : null

  return (
    <article
      className={`card ${selected ? 'selected' : ''}`}
      onClick={onClick}
      style={{ ['--i' as any]: index }}
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onClick() }}
    >
      <header className="card-hdr">
        <div>
          <div className="card-id">{d.district_id}</div>
          <h2 className="card-name">{d.name}</h2>
          <div className="card-state">
            <span>{d.state ?? '—'}</span>
            {d.website && <><span className="sep">·</span><span>{d.website}</span></>}
          </div>
        </div>
        <span className={`pill pill-${d.status}`}>{statusLabel(d.status)}</span>
      </header>

      <dl className="card-meta">
        <div>
          <dt>Enrollment</dt>
          <dd>{fmtEnrollment(d.enrollment)}</dd>
        </div>
        <div>
          <dt>Signals</dt>
          <dd>{d.signal_count}</dd>
        </div>
        <div>
          <dt>Fit</dt>
          <dd className={fitClass(d.enrichment?.fit_score)}>
            {d.enrichment?.fit_score ?? '—'}
          </dd>
        </div>
      </dl>

      {showDraftPreview && subject && (
        <div className="draft-preview">
          <div className="draft-preview-subj">{subject}</div>
          {bodyPreview && <div className="draft-preview-body">{bodyPreview}…</div>}
          {draft?.hook_summary && (
            <div className="draft-preview-hook"><strong>Hook</strong> {draft.hook_summary}</div>
          )}
        </div>
      )}

      {!showDraftPreview && d.intake_notes && <p className="notes">{d.intake_notes}</p>}

      {d.duplicate_of_external_id && (
        <p className="flag">Duplicate of {d.duplicate_of_external_id}</p>
      )}
      {d.non_fit_reason && (
        <p className="flag">Non-fit: {d.non_fit_reason}</p>
      )}
    </article>
  )
}

const PRIORITY_GROUPS: { key: District['send_priority']; label: string; sub: string }[] = [
  { key: 'send_today', label: 'Send today', sub: 'Strong fit or recent signal — top of queue.' },
  { key: 'this_week',  label: 'This week',  sub: 'Moderate fit; warm but not urgent.' },
  { key: 'later',      label: 'Later',      sub: 'Approved but lower priority. Re-evaluate if new signal arrives.' },
]

function PipelineGroupedView({ districts, openId, onOpen }: {
  districts: District[]; openId: string | null; onOpen: (id: string) => void
}) {
  const grouped = PRIORITY_GROUPS.map((g) => ({
    ...g,
    items: districts.filter((d) => (d.send_priority ?? 'later') === g.key),
  }))
  return (
    <div className="pipeline-view">
      {grouped.map((g) => g.items.length === 0 ? null : (
        <section key={g.key ?? 'later'} className="pipeline-group">
          <header className="pipeline-group-hdr">
            <h3>
              {g.label}
              <span className="pipeline-count">{g.items.length}</span>
            </h3>
            <p>{g.sub}</p>
          </header>
          <div className="grid">
            {g.items.map((d, i) => (
              <DistrictCard
                key={d.district_id}
                d={d}
                index={i}
                selected={openId === d.district_id}
                onClick={() => onOpen(d.district_id)}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

/* ─── Unmatched signals — topbar pill + modal ───────────────── */

type UnmatchedSignal = {
  signal_id: string
  type: string
  date?: string | null
  match_notes?: string | null
  payload: Record<string, unknown>
}

function UnmatchedSignalsButton() {
  const [open, setOpen] = useState(false)
  const [count, setCount] = useState<number | null>(null)
  const [items, setItems] = useState<UnmatchedSignal[]>([])
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const res = await fetch('/api/signals/unmatched')
      const data: UnmatchedSignal[] = await res.json()
      setItems(data)
      setCount(data.length)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    if (open) window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (count == null || count === 0) return null

  return (
    <>
      <span className="sep" />
      <button
        className="unmatched-pill"
        onClick={() => { setOpen(true); load() }}
        aria-label="Inspect unmatched signals"
      >
        ⚠ {count} unmatched signal{count === 1 ? '' : 's'}
      </button>
      {open && (
        <>
          <div className="modal-bg-light" onClick={() => setOpen(false)} />
          <div className="unmatched-modal" role="dialog" aria-label="Unmatched signals">
            <header className="unmatched-hdr">
              <div>
                <h3>Unmatched signals</h3>
                <p className="sub">
                  These didn't resolve to any district. The matcher recorded why; review and decide.
                </p>
              </div>
              <button className="ghost sm" onClick={() => setOpen(false)}>Close <span className="kbd">Esc</span></button>
            </header>
            <div className="unmatched-body">
              {loading && <p className="muted">Loading…</p>}
              {!loading && items.length === 0 && <p className="muted">Nothing unmatched. Good.</p>}
              {items.map((s) => (
                <article key={s.signal_id} className="unmatched-item">
                  <header>
                    <span className="card-id">{s.signal_id}</span>
                    <span className="pill pill-pending">{s.type.replace(/_/g, ' ')}</span>
                    {s.date && <span className="muted small">{s.date}</span>}
                  </header>
                  {s.match_notes && (
                    <p className="match-notes"><strong>Why unmatched.</strong> {s.match_notes}</p>
                  )}
                  <pre className="payload">{prettyPayload(s.payload)}</pre>
                </article>
              ))}
            </div>
          </div>
        </>
      )}
    </>
  )
}

function prettyPayload(p: Record<string, unknown>): string {
  const skip = new Set(['signal_id', 'type', 'date'])
  return Object.entries(p)
    .filter(([k]) => !skip.has(k))
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('\n')
}

/* ─── Quick action card ─────────────────────────────────────── */

function QuickCard({
  icon, iconClass, title, sub, count, onClick, disabled,
}: {
  icon: string
  iconClass: 'brief' | 'queue' | 'send' | 'chat'
  title: string
  sub: string
  count?: number | string
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button className="quick-card" onClick={onClick} disabled={disabled}>
      <span className={`quick-card-icon ${iconClass}`} aria-hidden="true">{icon}</span>
      <span className="quick-card-body">
        <span className="quick-card-title">
          <span>{title}</span>
          {count !== undefined && <span className="cnt">{count}</span>}
        </span>
        <span className="quick-card-sub">{sub}</span>
      </span>
    </button>
  )
}

/* ─── Inline SVG icons (Lucide-ish) ────────────────────────── */

function _Svg({ children, className = 'icon' }: { children: ReactNode; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  )
}

function PlusIcon() { return <_Svg><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></_Svg> }
function InboxIcon() { return <_Svg><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></_Svg> }
function BarsIcon() { return <_Svg><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></_Svg> }
function RefreshIcon({ spinning }: { spinning?: boolean }) {
  return <_Svg className={`icon ${spinning ? 'spin' : ''}`}><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></_Svg>
}
function ChevronIcon({ flipped }: { flipped?: boolean }) {
  return <_Svg className="icon" >{flipped ? <polyline points="6 9 12 15 18 9"/> : <polyline points="18 15 12 9 6 15"/>}</_Svg>
}
