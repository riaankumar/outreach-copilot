import { useEffect, useMemo, useState } from 'react'
import DistrictDetail from './DistrictDetail'
import type { District } from './types'
import './App.css'

const FILE_NO = (() => {
  // Deterministic-ish file number for the masthead based on the date.
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}-OPS`
})()

const DATE_LABEL = new Date().toLocaleDateString('en-US', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
  year: 'numeric',
})

function fmtEnrollment(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`
  return n.toLocaleString()
}

function statusLabel(s: string): string {
  return s.replace(/_/g, ' ')
}

export default function App() {
  const [districts, setDistricts] = useState<District[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [openId, setOpenId] = useState<string | null>(null)

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

  // Sort: actionable first (pending, enriched), then approved/edited, then non_fit/duplicate/rejected
  const sorted = useMemo(() => {
    const order: Record<string, number> = {
      enriched: 0,
      pending: 1,
      enriching: 2,
      edited: 3,
      approved: 4,
      duplicate: 5,
      non_fit: 6,
      rejected: 7,
    }
    return [...districts].sort((a, b) => (order[a.status] ?? 99) - (order[b.status] ?? 99))
  }, [districts])

  const totals = useMemo(() => {
    const t = { pending: 0, enriched: 0, approved: 0, signals: 0 }
    for (const d of districts) {
      if (d.status === 'pending') t.pending++
      if (d.status === 'enriched' || d.status === 'edited') t.enriched++
      if (d.status === 'approved') t.approved++
      t.signals += d.signal_count
    }
    return t
  }, [districts])

  return (
    <div className="app">
      <header>
        <div className="masthead">
          <div className="masthead-l">
            <strong>VOL. I</strong>
            File № {FILE_NO}
            <br />
            Classification: Internal
          </div>

          <h1 className="masthead-title">
            The <em>Bureau</em>
          </h1>

          <div className="masthead-r">
            <strong>{DATE_LABEL}</strong>
            Operator: R. Kumar
            <br />
            Dispatch — Journify
          </div>
        </div>

        <p className="masthead-sub">
          District Outreach Copilot · Grounded Intelligence for K-12 Sales
        </p>
      </header>

      <section className="toolbar">
        <h2>
          Active Dossiers
          <span className="count">
            {districts.length.toString().padStart(2, '0')} files
            <span style={{ margin: '0 10px', opacity: 0.4 }}>·</span>
            {totals.pending} pending
            <span style={{ margin: '0 10px', opacity: 0.4 }}>·</span>
            {totals.enriched} briefed
            <span style={{ margin: '0 10px', opacity: 0.4 }}>·</span>
            {totals.approved} dispatched
            <span style={{ margin: '0 10px', opacity: 0.4 }}>·</span>
            {totals.signals} signals matched
          </span>
        </h2>
        <button onClick={refresh} disabled={loading}>
          {loading ? 'Reloading…' : 'Refresh'}
        </button>
      </section>

      {error && <div className="err">{error}</div>}

      {!error && districts.length === 0 && !loading && (
        <div className="empty">
          <p>The cabinet is empty.</p>
          <p className="hint">
            Seed it: <code>uv run python scripts/seed.py</code>
          </p>
        </div>
      )}

      <div className="grid">
        {sorted.map((d, i) => (
          <article
            key={d.district_id}
            className="card"
            onClick={() => setOpenId(d.district_id)}
            style={{ ['--i' as any]: i }}
          >
            <header className="card-hdr">
              <div>
                <div className="card-id">{d.district_id}</div>
                <h2 className="card-name">{d.name}</h2>
                <div className="card-state">
                  {d.state ?? '—'} · {d.website ?? 'no website on file'}
                </div>
              </div>
              <span className={`stamp stamp-${d.status}`}>{statusLabel(d.status)}</span>
            </header>

            <dl className="card-meta">
              <div>
                <dt>Enrollment</dt>
                <dd>{fmtEnrollment(d.enrollment)}</dd>
              </div>
              <div>
                <dt>Signals</dt>
                <dd>{d.signal_count.toString().padStart(2, '0')}</dd>
              </div>
              <div>
                <dt>Fit</dt>
                <dd>{d.enrichment?.fit_score ?? '—'}</dd>
              </div>
            </dl>

            {d.intake_notes && <p className="notes">{d.intake_notes}</p>}

            {d.duplicate_of_external_id && (
              <p className="flag">Cross-ref: duplicate of {d.duplicate_of_external_id}</p>
            )}
            {d.non_fit_reason && <p className="flag">Non-fit: {d.non_fit_reason}</p>}
          </article>
        ))}
      </div>

      <footer className="ftr">
        <span>Compiled {DATE_LABEL}</span>
        <span>End of file</span>
      </footer>

      {openId && (
        <DistrictDetail
          districtId={openId}
          onClose={() => setOpenId(null)}
          onChanged={refresh}
        />
      )}
    </div>
  )
}
