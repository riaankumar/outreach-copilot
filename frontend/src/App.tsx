import { useEffect, useMemo, useState } from 'react'
import DistrictDetail from './DistrictDetail'
import type { District } from './types'
import './App.css'

type StatusFilter = 'all' | 'pending' | 'enriched' | 'approved' | 'archived'
type SortKey = 'fit' | 'signals' | 'enrollment' | 'name'

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
  if (f === 'enriched') return d.status === 'enriched' || d.status === 'edited'
  if (f === 'approved') return d.status === 'approved'
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
    const c = { all: districts.length, pending: 0, enriched: 0, approved: 0, archived: 0 }
    for (const d of districts) {
      if (d.status === 'pending' || d.status === 'enriching') c.pending++
      if (d.status === 'enriched' || d.status === 'edited') c.enriched++
      if (d.status === 'approved') c.approved++
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

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="dot" aria-hidden="true" />
            Journify <small>Outreach Copilot</small>
          </div>
          <div className="topbar-meta">
            <span>{counts.all} districts</span>
            <span className="sep" />
            <span>{counts.pending} pending review</span>
            <span className="sep" />
            <span>{counts.approved} dispatched</span>
          </div>
        </div>
      </header>

      <main className="app">
        <div className="page-hdr">
          <div>
            <h1>Districts</h1>
            <p className="lede">
              Review AI-briefed districts, edit the dispatch draft if needed, and approve outreach. Click any row to open the dossier.
            </p>
          </div>
          <button onClick={refresh} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        <div className="toolbar">
          <div className="filter-group" role="tablist" aria-label="Filter by status">
            <FilterBtn label="All" count={counts.all} active={filter === 'all'} onClick={() => setFilter('all')} />
            <FilterBtn label="Pending" count={counts.pending} active={filter === 'pending'} onClick={() => setFilter('pending')} />
            <FilterBtn label="Briefed" count={counts.enriched} active={filter === 'enriched'} onClick={() => setFilter('enriched')} />
            <FilterBtn label="Approved" count={counts.approved} active={filter === 'approved'} onClick={() => setFilter('approved')} />
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

        <div className="grid">
          {filteredSorted.map((d, i) => (
            <article
              key={d.district_id}
              className={`card ${openId === d.district_id ? 'selected' : ''}`}
              onClick={() => setOpenId(d.district_id)}
              style={{ ['--i' as any]: i }}
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setOpenId(d.district_id) }}
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

              {d.intake_notes && <p className="notes">{d.intake_notes}</p>}

              {d.duplicate_of_external_id && (
                <p className="flag">Duplicate of {d.duplicate_of_external_id}</p>
              )}
              {d.non_fit_reason && (
                <p className="flag">Non-fit: {d.non_fit_reason}</p>
              )}
            </article>
          ))}
        </div>

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
