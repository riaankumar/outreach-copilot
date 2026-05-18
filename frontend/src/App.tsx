import { useEffect, useState } from 'react'
import DistrictDetail from './DistrictDetail'
import type { District } from './types'
import './App.css'

const STATUS_COLORS: Record<string, string> = {
  pending: '#64748b',
  enriching: '#0ea5e9',
  enriched: '#10b981',
  approved: '#22c55e',
  rejected: '#ef4444',
  non_fit: '#94a3b8',
  duplicate: '#a855f7',
}

function fmtEnrollment(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toLocaleString()
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

  return (
    <div className="app">
      <header className="hdr">
        <div>
          <h1>District Outreach Copilot</h1>
          <p className="sub">Journify take-home · Riaan Kumar</p>
        </div>
        <button onClick={refresh} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </header>

      {error && <div className="err">Error: {error}</div>}

      {!error && districts.length === 0 && !loading && (
        <div className="empty">
          <p>No districts yet.</p>
          <p className="hint">
            Seed the DB by POSTing <code>target_districts.json</code> to <code>/api/districts/bulk</code>.
          </p>
        </div>
      )}

      <div className="grid">
        {districts.map((d) => (
          <article
            key={d.district_id}
            className="card"
            onClick={() => setOpenId(d.district_id)}
            style={{ cursor: 'pointer' }}
          >
            <header className="card-hdr">
              <div className="card-id">{d.district_id}</div>
              <span
                className="badge"
                style={{ background: STATUS_COLORS[d.status] ?? '#64748b' }}
              >
                {d.status}
              </span>
            </header>
            <h2 className="card-name">{d.name}</h2>
            <dl className="card-meta">
              <div>
                <dt>State</dt>
                <dd>{d.state ?? '—'}</dd>
              </div>
              <div>
                <dt>Enrollment</dt>
                <dd>{fmtEnrollment(d.enrollment)}</dd>
              </div>
              <div>
                <dt>Signals</dt>
                <dd>{d.signal_count}</dd>
              </div>
            </dl>
            {d.website && (
              <a className="site" href={`https://${d.website}`} target="_blank" rel="noreferrer">
                {d.website} ↗
              </a>
            )}
            {d.intake_notes && <p className="notes">{d.intake_notes}</p>}
            {d.duplicate_of_external_id && (
              <p className="flag">Duplicate of {d.duplicate_of_external_id}</p>
            )}
            {d.non_fit_reason && <p className="flag">Non-fit: {d.non_fit_reason}</p>}
          </article>
        ))}
      </div>

      <footer className="ftr">
        {districts.length} district{districts.length === 1 ? '' : 's'} · API :8000 · UI :5173
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
