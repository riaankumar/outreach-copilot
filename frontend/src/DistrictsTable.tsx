import { useMemo, useState, type ReactNode } from 'react'
import type { District } from './types'

type Props = {
  districts: District[]
  openId: string | null
  onOpen: (id: string) => void
  query: string
}

type SortField = 'district_id' | 'name' | 'state' | 'enrollment' | 'signal_count' | 'fit' | 'status' | 'priority'
type SortDir = 'asc' | 'desc'

const PRIORITY_ORDER: Record<string, number> = {
  send_today: 0, this_week: 1, later: 2,
}

const STATUS_ORDER: Record<string, number> = {
  pending: 0, enriching: 1, enriched: 2, edited: 3, approved: 4, sent: 5, rejected: 6, non_fit: 7, duplicate: 8,
}

export default function DistrictsTable({ districts, openId, onOpen, query }: Props) {
  const [sortField, setSortField] = useState<SortField>('fit')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    let arr = districts
    if (q) {
      arr = arr.filter((d) =>
        d.name.toLowerCase().includes(q) ||
        (d.state ?? '').toLowerCase().includes(q) ||
        d.district_id.toLowerCase().includes(q),
      )
    }
    const get = (d: District): number | string => {
      switch (sortField) {
        case 'district_id': return d.district_id
        case 'name': return d.name
        case 'state': return d.state ?? ''
        case 'enrollment': return d.enrollment ?? -1
        case 'signal_count': return d.signal_count
        case 'fit': return d.enrichment?.fit_score ?? -1
        case 'status': return STATUS_ORDER[d.status] ?? 99
        case 'priority': return PRIORITY_ORDER[d.send_priority ?? 'later'] ?? 99
      }
    }
    const sign = sortDir === 'asc' ? 1 : -1
    return [...arr].sort((a, b) => {
      const av = get(a), bv = get(b)
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sign
      return String(av).localeCompare(String(bv)) * sign
    })
  }, [districts, query, sortField, sortDir])

  function toggleSort(field: SortField) {
    if (sortField === field) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      // sensible defaults: numeric/priority desc, text asc
      setSortDir(['enrollment', 'signal_count', 'fit'].includes(field) ? 'desc' : 'asc')
    }
  }

  return (
    <div className="table-wrap">
      <table className="dt">
        <thead>
          <tr>
            <th style={{ width: 36 }} className="dt-num">#</th>
            <Th label="ID" field="district_id" type="mono" {...{ sortField, sortDir, toggleSort }} />
            <Th label="District" field="name" type="text" {...{ sortField, sortDir, toggleSort }} />
            <Th label="State" field="state" type="text" {...{ sortField, sortDir, toggleSort }} />
            <Th label="Enrollment" field="enrollment" type="num" {...{ sortField, sortDir, toggleSort }} />
            <Th label="Signals" field="signal_count" type="num" {...{ sortField, sortDir, toggleSort }} />
            <Th label="Fit" field="fit" type="num" {...{ sortField, sortDir, toggleSort }} />
            <Th label="Status" field="status" type="status" {...{ sortField, sortDir, toggleSort }} />
            <Th label="Send priority" field="priority" type="status" {...{ sortField, sortDir, toggleSort }} />
            <th className="dt-text">Subject</th>
            <th className="dt-text">Recipient</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d, i) => {
            const draft = d.email_draft
            return (
              <tr
                key={d.district_id}
                className={openId === d.district_id ? 'sel' : ''}
                onClick={() => onOpen(d.district_id)}
              >
                <td className="dt-num dt-row-no">{i + 1}</td>
                <td className="dt-mono">{d.district_id}</td>
                <td className="dt-text dt-bold">{d.name}</td>
                <td className="dt-text">{d.state ?? '—'}</td>
                <td className="dt-num">{fmtEnrollment(d.enrollment)}</td>
                <td className="dt-num">{d.signal_count}</td>
                <td className="dt-num">
                  <span className={`fit-cell ${fitClass(d.enrichment?.fit_score)}`}>
                    {d.enrichment?.fit_score ?? '—'}
                  </span>
                </td>
                <td className="dt-text">
                  <span className={`pill pill-${d.status}`}>{d.status.replace(/_/g, ' ')}</span>
                </td>
                <td className="dt-text">
                  {d.send_priority
                    ? <span className={`pri pri-${d.send_priority}`}>{d.send_priority.replace(/_/g, ' ')}</span>
                    : <span className="muted small">—</span>}
                </td>
                <td className="dt-text dt-truncate">
                  {draft ? (draft.edited_subject ?? draft.subject) : <span className="muted small">—</span>}
                </td>
                <td className="dt-text dt-truncate">
                  {draft?.recipient_name
                    ? <span>{draft.recipient_name}{draft.recipient_title ? <span className="muted small"> · {draft.recipient_title}</span> : null}</span>
                    : <span className="muted small">—</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {rows.length === 0 && (
        <div className="dt-empty">No rows match this search.</div>
      )}
    </div>
  )
}

function Th({ label, field, type, sortField, sortDir, toggleSort }: {
  label: string
  field: SortField
  type: 'text' | 'num' | 'mono' | 'status'
  sortField: SortField
  sortDir: SortDir
  toggleSort: (f: SortField) => void
}) {
  const active = sortField === field
  return (
    <th
      className={`dt-${type === 'num' ? 'num' : 'text'} dt-th ${active ? 'on' : ''}`}
      onClick={() => toggleSort(field)}
    >
      <span className="dt-th-inner">
        <ThIcon type={type} />
        {label}
        <ThArrow active={active} dir={sortDir} />
      </span>
    </th>
  )
}

function ThIcon({ type }: { type: 'text' | 'num' | 'mono' | 'status' }) {
  if (type === 'num') return <Glyph>123</Glyph>
  if (type === 'mono') return <Glyph>ID</Glyph>
  if (type === 'status') return <Glyph>◉</Glyph>
  return <Glyph>T</Glyph>
}

function Glyph({ children }: { children: ReactNode }) {
  return <span className="dt-th-glyph">{children}</span>
}

function ThArrow({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="dt-th-arrow off">⇅</span>
  return <span className="dt-th-arrow">{dir === 'asc' ? '↑' : '↓'}</span>
}

function fmtEnrollment(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`
  return n.toLocaleString()
}

function fitClass(score: number | null | undefined): string {
  if (score == null) return 'empty'
  if (score >= 75) return 'good'
  if (score >= 50) return 'mid'
  return 'weak'
}
