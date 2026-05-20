import { useEffect, useState } from 'react'

type UnmatchedSignal = {
  signal_id: string
  type: string
  date?: string | null
  match_notes?: string | null
  payload: Record<string, unknown>
}

type Props = {
  loading?: boolean
  onRefresh?: () => void
}

export default function UnmatchedPage({ onRefresh }: Props) {
  const [items, setItems] = useState<UnmatchedSignal[]>([])
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try {
      const res = await fetch('/api/signals/unmatched')
      setItems(await res.json())
    } finally {
      setLoading(false)
    }
  }

  async function rerun() {
    setLoading(true)
    try {
      await fetch('/api/signals/resolve', { method: 'POST' })
      await load()
      onRefresh?.()
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div>
      <div className="page-hdr">
        <div className="page-hdr-left">
          <span className="page-hdr-icon warn" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 12h2a8 8 0 0 1 16 0h2"/>
              <path d="M6 12h2a4 4 0 0 1 8 0h2"/>
              <circle cx="12" cy="12" r="1.5"/>
            </svg>
          </span>
          <div>
            <h1>Unmatched signals</h1>
            <p className="lede">
              These signals didn't resolve to any district. The matcher recorded why next to each.
              Re-run after ingesting new districts to give old signals another chance.
            </p>
          </div>
        </div>
        <div className="page-hdr-actions">
          <button className="ghost" onClick={load} disabled={loading} aria-label="Refresh list">Refresh</button>
          <button className="primary" onClick={rerun} disabled={loading}>
            Re-run matcher
          </button>
        </div>
      </div>

      {loading && items.length === 0 ? (
        <div className="empty"><p>Loading…</p></div>
      ) : items.length === 0 ? (
        <div className="empty">
          <span className="empty-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
          </span>
          <h3>Every signal matched a district</h3>
          <p>Good. If you ingest more signals later, anything that doesn't resolve will appear here.</p>
        </div>
      ) : (
        <div className="unmatched-list">
          {items.map((s) => (
            <article key={s.signal_id} className="unmatched-row">
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
      )}
    </div>
  )
}

function prettyPayload(p: Record<string, unknown>): string {
  const skip = new Set(['signal_id', 'type', 'date'])
  return Object.entries(p)
    .filter(([k]) => !skip.has(k))
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('\n')
}
