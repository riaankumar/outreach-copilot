import { useEffect, useState } from 'react'
import type { Citation, District, EmailDraft, PipelineResult, Signal } from './types'

type Props = {
  districtId: string
  onClose: () => void
  onChanged: () => void
}

const STATUS_COLORS: Record<string, string> = {
  pending: '#64748b',
  enriching: '#0ea5e9',
  enriched: '#10b981',
  approved: '#22c55e',
  rejected: '#ef4444',
  edited: '#f59e0b',
  non_fit: '#94a3b8',
  duplicate: '#a855f7',
  draft: '#0ea5e9',
}

export default function DistrictDetail({ districtId, onClose, onChanged }: Props) {
  const [district, setDistrict] = useState<District | null>(null)
  const [signals, setSignals] = useState<Signal[]>([])
  const [running, setRunning] = useState(false)
  const [pipelineLog, setPipelineLog] = useState<PipelineResult | null>(null)
  const [editedSubject, setEditedSubject] = useState('')
  const [editedBody, setEditedBody] = useState('')
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  async function load() {
    const [dResp, sResp] = await Promise.all([
      fetch(`/api/districts/${districtId}`).then((r) => r.json()),
      fetch(`/api/districts/${districtId}/signals`).then((r) => r.json()),
    ])
    setDistrict(dResp)
    setSignals(sResp)
    if (dResp.email_draft) {
      setEditedSubject(dResp.email_draft.edited_subject ?? dResp.email_draft.subject)
      setEditedBody(dResp.email_draft.edited_body ?? dResp.email_draft.body)
    }
  }

  useEffect(() => {
    load()
  }, [districtId])

  async function runPipeline() {
    setRunning(true)
    setActionMsg(null)
    try {
      const res = await fetch(`/api/districts/${districtId}/run-pipeline`, { method: 'POST' })
      const body: PipelineResult = await res.json()
      setPipelineLog(body)
      await load()
      onChanged()
    } catch (e: any) {
      setActionMsg(`Pipeline error: ${e.message ?? e}`)
    } finally {
      setRunning(false)
    }
  }

  async function postDraftAction(action: 'approve' | 'edit' | 'reject', reason?: string) {
    if (!district?.email_draft) return
    const draftId = (district.email_draft as any).id
    const body: any = { action }
    if (action === 'edit') {
      body.edited_subject = editedSubject
      body.edited_body = editedBody
    }
    if (action === 'reject') {
      body.rejection_reason = reason ?? 'no reason given'
    }
    const res = await fetch(`/api/email-drafts/${draftId}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    const out = await res.json()
    setActionMsg(`Draft ${out.status}, district now ${out.district_status}`)
    await load()
    onChanged()
  }

  if (!district) {
    return (
      <div className="modal-bg" onClick={onClose}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>
          <p style={{ padding: 24 }}>Loading…</p>
        </div>
      </div>
    )
  }

  const canRunPipeline = !['duplicate', 'non_fit', 'rejected'].includes(district.status)
  const e = district.enrichment
  const draft = district.email_draft

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(ev) => ev.stopPropagation()}>
        <header className="modal-hdr">
          <div>
            <div className="card-id">{district.district_id}</div>
            <h2 style={{ margin: '4px 0 0' }}>{district.name}</h2>
            <p className="sub" style={{ margin: '4px 0 0' }}>
              {district.state ?? '—'} · enrollment {district.enrollment?.toLocaleString() ?? '—'}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="badge" style={{ background: STATUS_COLORS[district.status] ?? '#64748b' }}>
              {district.status}
            </span>
            {canRunPipeline && (
              <button onClick={runPipeline} disabled={running}>
                {running ? 'Running…' : (e ? 'Re-run' : 'Run pipeline')}
              </button>
            )}
            <button onClick={onClose} className="ghost">Close</button>
          </div>
        </header>

        {actionMsg && <div className="msg">{actionMsg}</div>}
        {pipelineLog && (
          <div className="pipeline-log">
            {pipelineLog.steps.map((s, i) => <div key={i}>✓ {s}</div>)}
            {pipelineLog.errors.map((s, i) => <div key={`e${i}`} className="err-line">✗ {s}</div>)}
          </div>
        )}

        {district.intake_notes && (
          <section className="panel">
            <h3>Intake</h3>
            <p>{district.intake_notes}</p>
          </section>
        )}

        <section className="panel">
          <h3>Resolved signals ({signals.length})</h3>
          {signals.length === 0 && <p className="muted">No signals resolved to this district.</p>}
          {signals.map((s) => (
            <article key={s.signal_id} className="signal">
              <header>
                <span className="card-id">{s.signal_id}</span>
                <span className="pill">{s.type}</span>
                <span className="muted">{s.date}</span>
                <span className="muted">
                  {s.match_strategy} · {s.match_confidence != null ? `${(s.match_confidence * 100).toFixed(0)}%` : '—'}
                </span>
              </header>
              <pre>{summarizeSignal(s.payload)}</pre>
            </article>
          ))}
        </section>

        {e && (
          <section className="panel">
            <h3>
              Enrichment
              {e.fit_score != null && <span className="fit">fit {e.fit_score}</span>}
            </h3>
            <Field label="Region" value={e.region_context} cites={e.citations} field="region_context" />
            <Field label="SPED footprint" value={describeSped(e)} cites={e.citations} field="iep_pct" />
            <Field label="Decision-maker" value={describeDecisionMaker(e)} cites={e.citations} field="sped_director_name" />
            <Field label="Recent initiatives" value={e.recent_initiatives} cites={e.citations} field="recent_initiatives" />
            <Field label="Pain points" value={e.pain_points} cites={e.citations} field="pain_points" />
            <Field label="Fit reasoning" value={e.fit_reasoning} cites={e.citations} field="fit_reasoning" />
            {e.fit_breakdown && (
              <div className="fit-grid">
                {Object.entries(e.fit_breakdown).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k.replace(/_/g, ' ')}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {draft && (
          <section className="panel">
            <h3>
              Email draft
              <span className="badge sm" style={{ background: STATUS_COLORS[draft.status] ?? '#64748b' }}>
                {draft.status}
              </span>
            </h3>
            <p className="muted small">
              To: {draft.recipient_name ?? '—'} ({draft.recipient_title ?? 'no title'})
              {draft.recipient_email ? ` · ${draft.recipient_email}` : ''}
            </p>
            {draft.hook_summary && <p className="muted small"><strong>Hook:</strong> {draft.hook_summary}</p>}

            <label className="lbl">Subject</label>
            <input
              className="inp"
              value={editedSubject}
              onChange={(ev) => setEditedSubject(ev.target.value)}
              disabled={['approved', 'rejected'].includes(draft.status)}
            />

            <label className="lbl">Body</label>
            <textarea
              className="inp"
              rows={12}
              value={editedBody}
              onChange={(ev) => setEditedBody(ev.target.value)}
              disabled={['approved', 'rejected'].includes(draft.status)}
            />

            <CitationList cites={draft.citations} />

            {!['approved', 'rejected'].includes(draft.status) && (
              <div className="actions">
                <button onClick={() => postDraftAction('approve')}>Approve</button>
                <button onClick={() => postDraftAction('edit')} className="secondary">Save edits</button>
                <button
                  onClick={() => {
                    const reason = prompt('Reject reason?') || 'no reason given'
                    postDraftAction('reject', reason)
                  }}
                  className="danger"
                >
                  Reject
                </button>
              </div>
            )}
          </section>
        )}

        {!e && district.status === 'pending' && (
          <section className="panel muted">
            <p>Not enriched yet. Click "Run pipeline" to generate enrichment + email draft.</p>
          </section>
        )}

        {district.non_fit_reason && (
          <section className="panel">
            <h3>Non-fit reason</h3>
            <p>{district.non_fit_reason}</p>
          </section>
        )}
        {district.duplicate_of_external_id && (
          <section className="panel">
            <h3>Duplicate</h3>
            <p>This record duplicates {district.duplicate_of_external_id}. Consolidated lifecycle on the older row.</p>
          </section>
        )}
      </div>
    </div>
  )
}

function Field({ label, value, cites, field }: { label: string; value: string | null | undefined; cites: Citation[]; field: string }) {
  if (!value) return null
  const matching = cites.filter((c) => c.field_name === field)
  return (
    <div className="fld">
      <dt>{label}</dt>
      <dd>
        {value}
        {matching.map((c, i) => (
          <CiteChip key={i} c={c} />
        ))}
      </dd>
    </div>
  )
}

function CiteChip({ c }: { c: Citation }) {
  const label =
    c.source_type === 'signal' ? c.source_signal_external_id ?? 'signal' :
    c.source_type === 'intake_note' ? 'intake' :
    c.source_type === 'inference' ? `inf${c.confidence != null ? ` ${(c.confidence * 100).toFixed(0)}%` : ''}` :
    c.source_type
  const title = c.source_quote ?? c.source_url ?? ''
  return <span className={`chip chip-${c.source_type}`} title={title}>{label}</span>
}

function CitationList({ cites }: { cites: Citation[] }) {
  if (!cites?.length) return null
  return (
    <details className="citations">
      <summary>{cites.length} citation{cites.length === 1 ? '' : 's'}</summary>
      <ul>
        {cites.map((c, i) => (
          <li key={i}>
            <strong>{c.field_name}</strong> <span className={`chip chip-${c.source_type}`}>{c.source_type}</span>
            {c.source_signal_external_id && <span> {c.source_signal_external_id}</span>}
            {c.confidence != null && <span className="muted"> · conf {(c.confidence * 100).toFixed(0)}%</span>}
            {c.source_quote && <div className="quote">"{c.source_quote}"</div>}
          </li>
        ))}
      </ul>
    </details>
  )
}

function summarizeSignal(p: Record<string, unknown>): string {
  const keys = Object.keys(p).filter((k) => k !== 'signal_id' && k !== 'type' && k !== 'date')
  const lines = keys.map((k) => `${k}: ${typeof p[k] === 'string' ? p[k] : JSON.stringify(p[k])}`)
  return lines.join('\n')
}

function describeSped(e: any): string | null {
  const parts: string[] = []
  if (e.iep_pct != null) parts.push(`${e.iep_pct}% IEP`)
  if (e.iep_count_estimate != null) parts.push(`~${e.iep_count_estimate.toLocaleString()} students on IEPs`)
  if (e.sped_program_notes) parts.push(e.sped_program_notes)
  return parts.length ? parts.join(' · ') : null
}

function describeDecisionMaker(e: any): string | null {
  const parts: string[] = []
  if (e.sped_director_name) parts.push(e.sped_director_name)
  if (e.sped_director_title) parts.push(e.sped_director_title)
  if (e.sped_director_email) parts.push(e.sped_director_email)
  return parts.length ? parts.join(' · ') : null
}
