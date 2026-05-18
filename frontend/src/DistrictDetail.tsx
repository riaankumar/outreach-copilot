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
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
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
    setActionMsg(`Draft ${out.status} — file now ${out.district_status}.`)
    await load()
    onChanged()
  }

  // Indexed citations per panel — for footnote-style superscript chips.
  const enrichmentCites = district?.enrichment?.citations ?? []
  const draftCites = district?.email_draft?.citations ?? []

  const enrichmentNoteIndex = useMemo(() => makeIndex(enrichmentCites), [enrichmentCites])
  const draftNoteIndex = useMemo(() => makeIndex(draftCites), [draftCites])

  if (!district) {
    return (
      <div className="modal-bg" onClick={onClose}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>
          <div style={{ padding: 48, fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.2em', color: 'var(--muted)', textTransform: 'uppercase' }}>
            Retrieving file…
          </div>
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
            <div className="modal-eyebrow">
              <span>File №&nbsp;{district.district_id}</span>
              <span className={`stamp lg stamp-${district.status}`}>{statusLabel(district.status)}</span>
            </div>
            <h2 className="modal-title">{district.name}</h2>
            <p className="modal-sub">
              {district.state ?? '—'}
              {' · '}
              Enrollment {district.enrollment?.toLocaleString() ?? '—'}
              {district.website ? ` · ${district.website}` : ''}
            </p>
          </div>
          <div className="modal-actions">
            {canRunPipeline && (
              <button
                onClick={runPipeline}
                disabled={running}
                className={`primary ${running ? 'running' : ''}`}
              >
                {running ? 'Processing' : e ? 'Re-brief' : 'Brief district'}
              </button>
            )}
            <button onClick={onClose} className="ghost">Close</button>
          </div>
        </header>

        <div className="modal-body">
          {actionMsg && <div className="msg">{actionMsg}</div>}
          {pipelineLog && (
            <div className="pipeline-log">
              {pipelineLog.steps.map((s, i) => <div key={i}>› {s}</div>)}
              {pipelineLog.errors.map((s, i) => <div key={`e${i}`} className="err-line">⚠ {s}</div>)}
            </div>
          )}

          {district.intake_notes && (
            <section className="panel">
              <h3>Intake</h3>
              <p style={{
                fontFamily: 'var(--body)',
                fontStyle: 'italic',
                fontSize: 17,
                lineHeight: 1.55,
                color: 'var(--text)',
                margin: 0,
              }}>
                "{district.intake_notes}"
              </p>
            </section>
          )}

          <section className="panel">
            <h3>Signals on file · {signals.length}</h3>
            {signals.length === 0 && (
              <p style={{ fontStyle: 'italic', color: 'var(--muted)', margin: 0 }}>
                No intent signals have been resolved to this district.
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
                  <br />
                  {summarizeSignal(s.payload)}
                </div>
                <div className="signal-r">
                  {s.match_strategy?.replace(/_/g, ' ') ?? '—'}
                  <span className="conf">
                    {s.match_confidence != null ? `${(s.match_confidence * 100).toFixed(0)}% conf` : ''}
                  </span>
                </div>
              </div>
            ))}
          </section>

          {e && (
            <section className="panel">
              <h3>Assessment</h3>

              {e.fit_score != null && (
                <div className="fit-plate">
                  {e.fit_score}<small>/ 100 fit</small>
                </div>
              )}

              <Field label="Region" value={e.region_context} cites={enrichmentNoteIndex.byField('region_context')} />
              <Field label="SPED footprint" value={describeSped(e)} cites={enrichmentNoteIndex.byField('iep_pct').concat(enrichmentNoteIndex.byField('iep_count_estimate'), enrichmentNoteIndex.byField('sped_program_notes'))} />
              <Field label="Decision-maker" value={describeDecisionMaker(e)} cites={enrichmentNoteIndex.byField('sped_director_name').concat(enrichmentNoteIndex.byField('sped_director_email'))} />
              <Field label="Recent initiatives" value={e.recent_initiatives} cites={enrichmentNoteIndex.byField('recent_initiatives')} />
              <Field label="Pain points" value={e.pain_points} cites={enrichmentNoteIndex.byField('pain_points')} />
              <Field label="Reasoning" value={e.fit_reasoning} cites={enrichmentNoteIndex.byField('fit_reasoning')} />

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

              <CitationList title="Footnotes (assessment)" cites={enrichmentNoteIndex.list} />
            </section>
          )}

          {draft && (
            <section className="panel">
              <h3>
                Dispatch draft
                <span className={`stamp stamp-${draft.status}`} style={{ marginLeft: 4 }}>{draft.status}</span>
              </h3>

              <div className="draft-meta">
                <span><strong>To.</strong> {draft.recipient_name ?? '—'}</span>
                {draft.recipient_title && <span>{draft.recipient_title}</span>}
                {draft.recipient_email && <span>{draft.recipient_email}</span>}
                {draft.hook_summary && (
                  <span style={{ flexBasis: '100%', marginTop: 6 }}>
                    <strong>Hook.</strong> <span style={{ fontFamily: 'var(--body)', fontStyle: 'italic', fontSize: 14, textTransform: 'none', letterSpacing: 0, color: 'var(--text-dim)' }}>{draft.hook_summary}</span>
                  </span>
                )}
              </div>

              <label className="lbl">Subject line</label>
              <input
                className="inp"
                value={editedSubject}
                onChange={(ev) => setEditedSubject(ev.target.value)}
                disabled={['approved', 'rejected'].includes(draft.status)}
              />

              <label className="lbl">Body</label>
              <DraftBody
                value={editedBody}
                onChange={setEditedBody}
                disabled={['approved', 'rejected'].includes(draft.status)}
              />

              <CitationList title="Footnotes (dispatch)" cites={draftNoteIndex.list} />

              {!['approved', 'rejected'].includes(draft.status) && (
                <div className="actions">
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
                  <button onClick={() => postDraftAction('approve')} className="primary">
                    Approve & dispatch
                  </button>
                </div>
              )}
            </section>
          )}

          {!e && district.status === 'pending' && (
            <section className="panel">
              <p style={{ fontFamily: 'var(--body)', fontStyle: 'italic', color: 'var(--text-dim)', margin: 0 }}>
                This dossier has not yet been briefed. Click <strong>Brief district</strong> above to generate
                the assessment and a dispatch draft from the signals on file.
              </p>
            </section>
          )}

          {district.non_fit_reason && (
            <section className="panel">
              <h3>Filed as non-fit</h3>
              <p style={{ fontFamily: 'var(--body)', fontSize: 15, color: 'var(--text-dim)', margin: 0 }}>
                {district.non_fit_reason}
              </p>
            </section>
          )}

          {district.duplicate_of_external_id && (
            <section className="panel">
              <h3>Cross-reference</h3>
              <p style={{ fontFamily: 'var(--body)', fontSize: 15, color: 'var(--text-dim)', margin: 0 }}>
                This file duplicates {district.duplicate_of_external_id}; lifecycle consolidated to the older record.
              </p>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}

/* ─── Field row with footnote-style superscripts ─────────────── */

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

/* ─── Email body with editable textarea + drop cap preview ───── */

function DraftBody({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled: boolean }) {
  return (
    <textarea
      className="inp"
      rows={12}
      value={value}
      onChange={(ev) => onChange(ev.target.value)}
      disabled={disabled}
    />
  )
}

/* ─── Citation list (numbered footnotes) ─────────────────────── */

function CitationList({ title, cites }: { title: string; cites: IndexedCitation[] }) {
  if (!cites.length) return null
  return (
    <details className="citations">
      <summary>{title} · {cites.length}</summary>
      <ol>
        {cites.map((c) => (
          <li key={c.n} id={`fn-${c.n}`}>
            <strong>{c.field_name}</strong>
            <span className={`src-type ${c.source_type}`}>{c.source_type}</span>
            {c.source_signal_external_id && (
              <span style={{ marginLeft: 8, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-dim)' }}>
                {c.source_signal_external_id}
              </span>
            )}
            {c.confidence != null && (
              <span style={{ marginLeft: 8, fontFamily: 'var(--mono)', fontSize: 10, letterSpacing: '0.1em', color: 'var(--muted)' }}>
                conf {(c.confidence * 100).toFixed(0)}%
              </span>
            )}
            {c.source_quote && <div className="quote">"{c.source_quote}"</div>}
            {c.source_url && (
              <div style={{ marginTop: 4, fontFamily: 'var(--mono)', fontSize: 11 }}>
                <a href={c.source_url} target="_blank" rel="noreferrer" style={{ color: 'var(--amber)' }}>
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

/* ─── Helpers ──────────────────────────────────────────────── */

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
