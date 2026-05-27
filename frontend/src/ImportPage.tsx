import { useRef, useState } from 'react'
import { apiUrl } from './api'

type AckResult = {
  ok: boolean
  accepted?: number
  skipped?: number
  skipped_reasons?: string[]
  error?: string
} | null

type Props = {
  onIngested: () => void
}

const DISTRICT_SCHEMA = `[
  {
    "district_id": "D001",
    "name": "Larkspur Unified School District",
    "state": "CA",
    "website": "larkspurusd.org",
    "enrollment": 18500,
    "intake_notes": "Inbound — superintendent's office submitted contact form 4/1"
  }
]`

const SIGNAL_SCHEMA = `[
  {
    "signal_id": "SIG001",
    "type": "webinar_attendance",
    "date": "2026-03-14",
    "attendee_email": "j.peterson@grandviewisd.org",
    "attendee_name": "James Peterson",
    "attendee_title": "Director of Special Education",
    "district_id": "D023"
  }
]`

export default function ImportPage({ onIngested }: Props) {
  return (
    <div>
      <div className="page-hdr">
        <div className="page-hdr-left">
          <span className="page-hdr-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
          </span>
          <div>
            <h1>Import data</h1>
            <p className="lede">
              Upload a JSON file or paste records. Best-effort batch ingest: bad records are
              skipped with a reason; the rest land.
            </p>
          </div>
        </div>
      </div>

      <div className="import-grid">
        <ImportCard
          title="Districts"
          subtitle="Target districts you want the AI to brief and rank."
          endpoint="/api/districts/bulk"
          wrapKey="target_districts"
          schemaExample={DISTRICT_SCHEMA}
          onIngested={onIngested}
        />
        <ImportCard
          title="Signals"
          subtitle="Intent signals — webinar attendances, downloads, RFPs, contact forms, LinkedIn engagement."
          endpoint="/api/signals/bulk"
          wrapKey="signals"
          schemaExample={SIGNAL_SCHEMA}
          onIngested={onIngested}
        />
      </div>

      <section className="sources">
        <h3>Where SDRs source data from</h3>
        <ul>
          <li><strong>Marketing webhooks</strong> — webinar registrations, whitepaper downloads, contact-form submissions stream in as signals.</li>
          <li><strong>CRM exports</strong> — bulk-export target districts (CSV → JSON via a quick convert) and POST here.</li>
          <li><strong>Outbound sourcing lists</strong> — Apollo, ZoomInfo, ASU+GSV Summit attendees, NCES district directories.</li>
          <li><strong>Procurement watchers</strong> — RFPs from state procurement sites, manually copied.</li>
          <li><strong>Referrals</strong> — note them as signals with <code>type=referral</code> and <code>referred_district_text</code>.</li>
        </ul>
        <p className="muted small">
          The included sample data lives at <code>~/Downloads/target_districts.json</code> and
          <code>~/Downloads/signals.json</code> (loaded by <code>scripts/seed.py</code>). Any
          new ingest goes through the same matcher + edge-case sweep + auto-resolve as the
          seed script.
        </p>
      </section>
    </div>
  )
}

function ImportCard({ title, subtitle, endpoint, wrapKey, schemaExample, onIngested }: {
  title: string
  subtitle: string
  endpoint: string
  wrapKey: string
  schemaExample: string
  onIngested: () => void
}) {
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<AckResult>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  function pickFile() { fileRef.current?.click() }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    const content = await f.text()
    setText(content)
  }

  function loadExample() {
    setText(schemaExample)
  }

  async function submit() {
    if (!text.trim()) return
    setSubmitting(true)
    setResult(null)
    try {
      let parsed: any
      try {
        parsed = JSON.parse(text)
      } catch (e: any) {
        setResult({ ok: false, error: `Invalid JSON: ${e.message}` })
        return
      }
      // Accept either a wrapped object {wrapKey: [...]} or a bare list
      const body = Array.isArray(parsed) ? parsed : parsed
      const res = await fetch(apiUrl(endpoint), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) {
        setResult({ ok: false, error: data.detail ?? `${res.status} ${res.statusText}` })
        return
      }
      setResult({
        ok: true,
        accepted: data.accepted,
        skipped: data.skipped,
        skipped_reasons: data.skipped_reasons,
      })
      onIngested()
    } catch (e: any) {
      setResult({ ok: false, error: e.message ?? String(e) })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="import-card">
      <header>
        <h3>{title}</h3>
        <p className="sub">{subtitle}</p>
      </header>

      <div className="import-actions">
        <button onClick={pickFile} disabled={submitting}>
          📁 Upload .json file
        </button>
        <button onClick={loadExample} className="ghost" disabled={submitting}>
          Load schema example
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          onChange={onFile}
          style={{ display: 'none' }}
        />
      </div>

      <textarea
        className="inp import-textarea"
        placeholder={`Paste JSON here — either a bare list or {"${wrapKey}": [...]}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={10}
        disabled={submitting}
      />

      <div className="import-submit">
        <span className="muted small">
          POST <code>{endpoint}</code>
        </span>
        <button className="primary" onClick={submit} disabled={!text.trim() || submitting}>
          {submitting ? 'Ingesting…' : `Ingest ${title.toLowerCase()}`}
        </button>
      </div>

      {result && result.ok && (
        <div className="import-result ok">
          <strong>{result.accepted} accepted</strong>
          {(result.skipped ?? 0) > 0 && <>, <strong>{result.skipped} skipped</strong></>}
          {result.skipped_reasons && result.skipped_reasons.length > 0 && (
            <details>
              <summary>{result.skipped_reasons.length} skip reason(s)</summary>
              <ul>{result.skipped_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </details>
          )}
        </div>
      )}
      {result && !result.ok && (
        <div className="import-result err">{result.error}</div>
      )}
    </section>
  )
}
