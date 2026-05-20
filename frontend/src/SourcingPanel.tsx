import { useEffect, useState } from 'react'

type Person = {
  name?: string | null
  title?: string | null
  email?: string | null
  linkedin_url?: string | null
  source_url?: string | null
  is_predicted?: boolean
}

type Citation = {
  field_name: string
  source_url?: string | null
  quote?: string | null
}

type SourcedDistrict = {
  name: string
  state?: string | null
  city?: string | null
  website?: string | null
  nces_district_id?: string | null
  enrollment?: number | null
  school_count?: number | null
  district_type?: string | null
  email_domain?: string | null
  email_format?: string | null
  superintendent?: Person | null
  sped_director?: Person | null
  other_contacts?: Person[]
  key_hook?: string | null
  notes?: string | null
  citations?: Citation[]
  confidence: number
  flags?: string[]
}

type Props = {
  open: boolean
  onClose: () => void
  onAdded: (districtId: string) => void
}

const EXAMPLE_QUERIES = [
  'Berkeley Unified School District',
  'Houston ISD',
  'Boston Public Schools',
  'larkspurusd.org',
]

export default function SourcingPanel({ open, onClose, onAdded }: Props) {
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<SourcedDistrict | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && open) onClose() }
    if (open) window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  useEffect(() => {
    if (!open) {
      // Reset when closed
      setTimeout(() => {
        setResult(null); setError(null); setAdded(null)
      }, 300)
    }
  }, [open])

  async function search(q: string) {
    if (!q.trim() || searching) return
    setSearching(true); setError(null); setResult(null); setAdded(null)
    try {
      const res = await fetch('/api/sourcing/search', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ query: q.trim() }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `${res.status} ${res.statusText}`)
      }
      setResult(await res.json())
    } catch (e: any) {
      setError(e.message ?? String(e))
    } finally {
      setSearching(false)
    }
  }

  async function add() {
    if (!result || adding) return
    setAdding(true); setError(null)
    try {
      const res = await fetch('/api/sourcing/add', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sourced: result }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `${res.status} ${res.statusText}`)
      }
      const data = await res.json()
      setAdded(data.district_id)
      onAdded(data.district_id)
    } catch (e: any) {
      setError(e.message ?? String(e))
    } finally {
      setAdding(false)
    }
  }

  if (!open) return null

  return (
    <>
      <div className="modal-bg-light" onClick={onClose} />
      <aside className="sourcing-panel" role="dialog" aria-label="Source new district">
        <header className="sourcing-hdr">
          <div>
            <div className="sourcing-eyebrow">Source</div>
            <h3>Find a new district</h3>
            <p className="sub">
              Enter a district name or website URL. We search the public web,
              pull SPED leadership and the email pattern, and verify before you commit.
            </p>
          </div>
          <button className="ghost sm" onClick={onClose}>Close <span className="kbd">Esc</span></button>
        </header>

        <form
          className="sourcing-form"
          onSubmit={(e) => { e.preventDefault(); search(query) }}
        >
          <input
            className="inp"
            placeholder="District name or website (e.g. Berkeley Unified)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={searching || adding}
            autoFocus
          />
          <button type="submit" className="primary" disabled={!query.trim() || searching || adding}>
            {searching ? 'Sourcing…' : 'Source'}
          </button>
        </form>

        {!result && !searching && !error && (
          <div className="sourcing-tips">
            <div className="tips-title">Try one:</div>
            <div className="tips-row">
              {EXAMPLE_QUERIES.map((q) => (
                <button key={q} className="sm" onClick={() => { setQuery(q); search(q) }}>
                  {q}
                </button>
              ))}
            </div>
            <p className="tips-note">
              The agent uses live web search. It typically takes 20-40 seconds.
              Every field comes back with a source URL you can verify.
            </p>
          </div>
        )}

        {searching && (
          <div className="sourcing-loading">
            <div className="loading-step">› Searching the web for "{query}"…</div>
            <div className="loading-step">› Fetching the district website…</div>
            <div className="loading-step">› Extracting SPED leadership + email pattern…</div>
            <div className="loading-step muted">› Verifying DNS and email consistency…</div>
            <p className="muted small" style={{ marginTop: 12 }}>
              This usually takes 20-40 seconds.
            </p>
          </div>
        )}

        {error && (
          <div className="err" style={{ marginTop: 12 }}>{error}</div>
        )}

        {result && (
          <div className="sourcing-result">
            <div className="confidence-row">
              <ConfidenceMeter value={result.confidence} />
              {result.flags && result.flags.length > 0 && (
                <details className="flags">
                  <summary>{result.flags.length} verification flag{result.flags.length === 1 ? '' : 's'}</summary>
                  <ul>{result.flags.map((f, i) => <li key={i}>{f}</li>)}</ul>
                </details>
              )}
            </div>

            <section className="result-block">
              <h4>{result.name}</h4>
              <div className="result-meta">
                {[result.city, result.state].filter(Boolean).join(', ') || '—'}
                {result.website && <> · <a href={`https://${cleanWebsite(result.website)}`} target="_blank" rel="noreferrer">{cleanWebsite(result.website)}</a></>}
                {result.district_type && <> · {result.district_type}</>}
              </div>

              <dl className="kv">
                <KV label="Enrollment" v={result.enrollment?.toLocaleString()} />
                <KV label="Schools" v={result.school_count} />
                <KV label="NCES ID" v={result.nces_district_id} mono />
                <KV label="Email domain" v={result.email_domain} mono />
                <KV label="Email format" v={result.email_format} mono />
              </dl>
            </section>

            {result.sped_director?.name && (
              <section className="result-block">
                <h5>SPED leadership</h5>
                <PersonCard person={result.sped_director} />
              </section>
            )}

            {result.superintendent?.name && (
              <section className="result-block">
                <h5>Superintendent</h5>
                <PersonCard person={result.superintendent} />
              </section>
            )}

            {result.other_contacts && result.other_contacts.length > 0 && (
              <section className="result-block">
                <h5>Other contacts</h5>
                {result.other_contacts.map((p, i) => <PersonCard key={i} person={p} />)}
              </section>
            )}

            {result.key_hook && (
              <section className="result-block">
                <h5>Key hook</h5>
                <p className="key-hook">{result.key_hook}</p>
              </section>
            )}

            {result.citations && result.citations.length > 0 && (
              <details className="citations">
                <summary>{result.citations.length} citation{result.citations.length === 1 ? '' : 's'}</summary>
                <ol>
                  {result.citations.map((c, i) => (
                    <li key={i}>
                      <strong>{c.field_name}</strong>
                      {c.source_url && (
                        <> — <a href={c.source_url} target="_blank" rel="noreferrer">{shortUrl(c.source_url)}</a></>
                      )}
                      {c.quote && <div className="quote">"{c.quote}"</div>}
                    </li>
                  ))}
                </ol>
              </details>
            )}

            <div className="sourcing-actions">
              <button onClick={() => search(query)} disabled={searching}>Re-source</button>
              <div className="spacer" />
              {added ? (
                <span className="muted small">✓ Added as {added}</span>
              ) : (
                <button className="primary" onClick={add} disabled={adding}>
                  {adding ? 'Adding…' : 'Add to pipeline'}
                </button>
              )}
            </div>
          </div>
        )}
      </aside>
    </>
  )
}

function PersonCard({ person }: { person: Person }) {
  return (
    <div className="person">
      <div className="person-line">
        <strong>{person.name}</strong>
        {person.title && <span className="muted"> · {person.title}</span>}
      </div>
      {person.email && (
        <div className="person-line">
          <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{person.email}</span>
          {person.is_predicted && <span className="tag-predicted">predicted</span>}
        </div>
      )}
      {person.linkedin_url && (
        <a className="person-line linkedin" href={person.linkedin_url} target="_blank" rel="noreferrer">
          LinkedIn ↗
        </a>
      )}
    </div>
  )
}

function KV({ label, v, mono }: { label: string; v?: any; mono?: boolean }) {
  if (v == null || v === '') return null
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : ''}>{v}</dd>
    </div>
  )
}

function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const cls = value >= 0.75 ? 'good' : value >= 0.5 ? 'mid' : 'weak'
  return (
    <div className={`confidence ${cls}`}>
      <span className="label">Confidence</span>
      <span className="bar"><span style={{ width: `${pct}%` }} /></span>
      <span className="num">{pct}%</span>
    </div>
  )
}

function cleanWebsite(w: string): string {
  return w.replace(/^https?:\/\//, '').replace(/\/$/, '')
}

function shortUrl(u: string): string {
  return u.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0]
}
