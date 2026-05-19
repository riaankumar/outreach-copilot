import { useEffect, useMemo, useState } from 'react'
import type { Citation, District, PipelineResult, Signal } from './types'

type Props = {
  districtId: string
  onClose: () => void
  onChanged: () => void
}

function statusLabel(s: string): string {
  return s.replace(/_/g, ' ')
}

function fitClass(score: number | null | undefined): string {
  if (score == null) return ''
  if (score >= 75) return 'good'
  if (score >= 50) return 'mid'
  return ''
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

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
    const draftId = district.email_draft.id
    const body: any = { action }
    if (action === 'edit') {
      body.edited_subject = editedSubject
      body.edited_body = editedBody
    }
    if (action === 'reject') body.rejection_reason = reason ?? 'no reason given'

    const res = await fetch(`/api/email-drafts/${draftId}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    const out = await res.json()
    setActionMsg(`Draft ${out.status}. District is now ${out.district_status}.`)
    await load()
    onChanged()
  }

  const enrichmentCites = district?.enrichment?.citations ?? []
  const draftCites = district?.email_draft?.citations ?? []
  const enrichIdx = useMemo(() => makeIndex(enrichmentCites), [enrichmentCites])
  const draftIdx = useMemo(() => makeIndex(draftCites), [draftCites])

  if (!district) {
    return (
      <>
        <div className="panel-bg" onClick={onClose} />
        <aside className="panel" role="dialog" aria-labelledby="panel-title">
          <div style={{ padding: 32, color: 'var(--muted)', fontSize: 13 }}>Loading…</div>
        </aside>
      </>
    )
  }

  const canRunPipeline = !['duplicate', 'non_fit', 'rejected'].includes(district.status)
  const e = district.enrichment
  const draft = district.email_draft
  const draftLocked = draft && ['approved', 'rejected'].includes(draft.status)

  return (
    <>
      <div className="panel-bg" onClick={onClose} />
      <aside className="panel" role="dialog" aria-labelledby="panel-title">
        <div className="panel-hdr">
          <div className="panel-hdr-top">
            <span className="id">{district.district_id}</span>
            <span className={`pill pill-${district.status} lg`}>{statusLabel(district.status)}</span>
            <div style={{ flex: 1 }} />
            <button className="ghost sm" onClick={onClose} aria-label="Close panel">
              Close <span className="kbd">Esc</span>
            </button>
          </div>
          <h2 id="panel-title">{district.name}</h2>
          <p className="sub">
            {district.state ?? '—'}
            {district.enrollment != null && <> · {district.enrollment.toLocaleString()} students</>}
            {district.website && <> · <a href={`https://${district.website}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>{district.website}</a></>}
          </p>

          {canRunPipeline && (
            <div className="panel-hdr-actions">
              <button
                onClick={runPipeline}
                disabled={running}
                className={`accent ${running ? 'running' : ''}`}
              >
                {running ? 'Running pipeline' : e ? 'Re-run pipeline' : 'Brief this district'}
              </button>
              {e && <span style={{ fontSize: 12, color: 'var(--muted)' }}>Last briefed {fmtTime(e.generated_at)}</span>}
            </div>
          )}
        </div>

        <div className="panel-body">
          {actionMsg && <div className="msg">{actionMsg}</div>}

          {pipelineLog && (
            <div className="pipeline-log">
              {pipelineLog.steps.map((s, i) => <div key={i} className="ok-line">✓ {s}</div>)}
              {pipelineLog.errors.map((s, i) => <div key={`e${i}`} className="err-line">✗ {s}</div>)}
            </div>
          )}

          {district.intake_notes && (
            <section className="section">
              <h3>Intake note</h3>
              <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: 'var(--text-2)' }}>
                {district.intake_notes}
              </p>
            </section>
          )}

          <section className="section">
            <h3>
              Resolved signals
              <span className="h3-count">{signals.length}</span>
            </h3>
            {signals.length === 0 && (
              <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>
                No intent signals have been resolved to this district yet.
              </p>
            )}
            {signals.map((s) => (
              <div key={s.signal_id} className="signal">
                <div className="signal-l">
                  <span className="sid">{s.signal_id}</span>
                  <span className="sdate">{s.date ?? '—'}</span>
                </div>
                <div className="signal-c">
                  <span className="type">{s.type.replace(/_/g, ' ')}</span>
                  <div className="kv">{summarizeSignal(s.payload)}</div>
                </div>
                <div className="signal-r">
                  {s.match_strategy?.replace(/_/g, ' ') ?? '—'}
                  {s.match_confidence != null && (
                    <span className={`conf ${s.match_confidence < 0.85 ? 'low' : ''}`}>
                      {(s.match_confidence * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
              </div>
            ))}
          </section>

          {e && (
            <section className="section">
              <h3>
                Assessment
                <div className="spacer" />
                <span className="h3-count">{enrichIdx.list.length} citation{enrichIdx.list.length === 1 ? '' : 's'}</span>
              </h3>

              {e.fit_score != null && (
                <div className="fit-banner">
                  <div className={`fit-num ${fitClass(e.fit_score)}`}>
                    {e.fit_score}<small> / 100</small>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div className={`fit-bar ${fitClass(e.fit_score)}`}>
                      <span style={{ width: `${e.fit_score}%` }} />
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 6 }}>
                      Fit score · {e.fit_score >= 75 ? 'Strong' : e.fit_score >= 50 ? 'Moderate' : 'Weak'}
                    </div>
                  </div>
                </div>
              )}

              <Field label="Region" value={e.region_context} cites={enrichIdx.byField('region_context')} />
              <Field
                label="SPED footprint"
                value={describeSped(e)}
                cites={[
                  ...enrichIdx.byField('iep_pct'),
                  ...enrichIdx.byField('iep_count_estimate'),
                  ...enrichIdx.byField('sped_program_notes'),
                ]}
              />
              <Field
                label="Decision-maker"
                value={describeDecisionMaker(e)}
                cites={[
                  ...enrichIdx.byField('sped_director_name'),
                  ...enrichIdx.byField('sped_director_email'),
                ]}
              />
              <Field label="Recent activity" value={e.recent_initiatives} cites={enrichIdx.byField('recent_initiatives')} />
              <Field label="Pain points" value={e.pain_points} cites={enrichIdx.byField('pain_points')} />
              <Field label="Reasoning" value={e.fit_reasoning} cites={enrichIdx.byField('fit_reasoning')} />

              {e.fit_breakdown && (
                <dl className="fit-grid">
                  {Object.entries(e.fit_breakdown).map(([k, v]) => (
                    <div key={k}>
                      <dt>{k.replace(/_/g, ' ')}</dt>
                      <dd>{typeof v === 'number' ? v : '—'}</dd>
                    </div>
                  ))}
                </dl>
              )}

              <CitationList cites={enrichIdx.list} />
            </section>
          )}

          {draft && (
            <section className="section">
              <h3>
                Email draft
                <span className={`pill pill-${draft.status}`}>{draft.status}</span>
              </h3>

              <div className="draft-to">
                <span><strong>To</strong>{draft.recipient_name ?? '—'}</span>
                {draft.recipient_title && <span>{draft.recipient_title}</span>}
                {draft.recipient_email && <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{draft.recipient_email}</span>}
              </div>

              {draft.hook_summary && (
                <p className="draft-hook">
                  <strong>Hook</strong>{draft.hook_summary}
                </p>
              )}

              <label className="lbl">Subject</label>
              <input
                className="inp"
                type="text"
                value={editedSubject}
                onChange={(ev) => setEditedSubject(ev.target.value)}
                disabled={draftLocked}
              />

              <label className="lbl">Body</label>
              <textarea
                className="inp"
                rows={12}
                value={editedBody}
                onChange={(ev) => setEditedBody(ev.target.value)}
                disabled={draftLocked}
              />

              <CitationList cites={draftIdx.list} />

              {!draftLocked && (
                <div className="draft-actions">
                  <button onClick={() => postDraftAction('edit')}>Save edits</button>
                  <button
                    onClick={() => {
                      const reason = prompt('Reject reason?')
                      if (reason !== null) postDraftAction('reject', reason || 'no reason given')
                    }}
                    className="danger"
                  >
                    Reject
                  </button>
                  <div className="spacer" />
                  <button onClick={() => postDraftAction('approve')} className="accent">
                    Approve & dispatch
                  </button>
                </div>
              )}
            </section>
          )}

          {!e && district.status === 'pending' && (
            <section className="section">
              <p style={{ margin: 0, fontSize: 13.5, color: 'var(--muted)' }}>
                Not briefed yet. Click <strong style={{ color: 'var(--text)' }}>Brief this district</strong> to
                generate the assessment and a dispatch draft from the signals on file.
              </p>
            </section>
          )}

          {district.non_fit_reason && (
            <section className="section">
              <h3>Filed as non-fit</h3>
              <p style={{ margin: 0, fontSize: 13.5, color: 'var(--muted)' }}>{district.non_fit_reason}</p>
            </section>
          )}

          {district.duplicate_of_external_id && (
            <section className="section">
              <h3>Duplicate</h3>
              <p style={{ margin: 0, fontSize: 13.5, color: 'var(--muted)' }}>
                This record duplicates <strong style={{ color: 'var(--text)' }}>{district.duplicate_of_external_id}</strong>.
                Lifecycle was consolidated onto the older row.
              </p>
            </section>
          )}
        </div>
      </aside>
    </>
  )
}

/* ─── Subcomponents ───────────────────────────────────────────── */

function Field({ label, value, cites }: { label: string; value: string | null | undefined; cites: IndexedCitation[] }) {
  if (!value) return null
  return (
    <dl className="fld">
      <dt>{label}</dt>
      <dd>
        {value}
        {cites.map((c) => (
          <sup key={c.n} className="fn" title={titleFor(c)}>{c.n}</sup>
        ))}
      </dd>
    </dl>
  )
}

function CitationList({ cites }: { cites: IndexedCitation[] }) {
  if (!cites.length) return null
  return (
    <details className="citations">
      <summary>{cites.length} citation{cites.length === 1 ? '' : 's'} · click to inspect sources</summary>
      <ol>
        {cites.map((c) => (
          <li key={c.n}>
            <strong>{c.field_name}</strong>
            <span className={`src-type src-type-${c.source_type}`}>{c.source_type}</span>
            {c.source_signal_external_id && (
              <span className="meta">{c.source_signal_external_id}</span>
            )}
            {c.confidence != null && (
              <span className="meta">conf {(c.confidence * 100).toFixed(0)}%</span>
            )}
            {c.source_quote && <div className="quote">"{c.source_quote}"</div>}
            {c.source_url && (
              <div style={{ marginTop: 4, fontFamily: 'var(--mono)', fontSize: 11 }}>
                <a href={c.source_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
                  {c.source_url}
                </a>
              </div>
            )}
          </li>
        ))}
      </ol>
    </details>
  )
}

/* ─── Helpers ─────────────────────────────────────────────────── */

type IndexedCitation = Citation & { n: number }

function makeIndex(cites: Citation[]): { list: IndexedCitation[]; byField: (f: string) => IndexedCitation[] } {
  const list: IndexedCitation[] = cites.map((c, i) => ({ ...c, n: i + 1 }))
  return {
    list,
    byField: (f: string) => list.filter((c) => c.field_name === f),
  }
}

function titleFor(c: Citation): string {
  const bits: string[] = [c.source_type]
  if (c.source_signal_external_id) bits.push(c.source_signal_external_id)
  if (c.source_quote) bits.push(`"${c.source_quote}"`)
  if (c.confidence != null) bits.push(`conf ${(c.confidence * 100).toFixed(0)}%`)
  return bits.join(' — ')
}

function summarizeSignal(p: Record<string, unknown>): string {
  const skip = new Set(['signal_id', 'type', 'date'])
  const lines: string[] = []
  for (const [k, v] of Object.entries(p)) {
    if (skip.has(k)) continue
    const value = typeof v === 'string' ? v : JSON.stringify(v)
    lines.push(`${k.replace(/_/g, ' ')}: ${value}`)
  }
  return lines.join('\n')
}

function describeSped(e: any): string | null {
  const parts: string[] = []
  if (e.iep_pct != null) parts.push(`${e.iep_pct}% IEP`)
  if (e.iep_count_estimate != null) parts.push(`~${e.iep_count_estimate.toLocaleString()} students`)
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

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const now = new Date()
    const diffMin = Math.floor((now.getTime() - d.getTime()) / 60000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    if (diffMin < 24 * 60) return `${Math.floor(diffMin / 60)}h ago`
    return d.toLocaleDateString()
  } catch {
    return ''
  }
}
