import type { ReactNode } from 'react'

export type View = 'home' | 'queue' | 'pipeline' | 'sent' | 'archived' | 'table' | 'unmatched' | 'import'

export type SidebarAction = 'open_sourcing'

type Props = {
  active: View
  onSelect: (v: View) => void
  queueCount: number
  pipelineCount: number
  sentCount: number
  archivedCount: number
  unmatchedCount: number
  onOpenSourcing: () => void
}

export default function Sidebar({
  active, onSelect, queueCount, pipelineCount, sentCount, archivedCount,
  unmatchedCount, onOpenSourcing,
}: Props) {
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="sidebar-brand">
        <span className="sidebar-brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            {/* dashed loop trail */}
            <path
              d="M5 18 Q3 12 8 9 Q14 6 12 14"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeDasharray="2 2.5"
              fill="none"
            />
            {/* paper airplane */}
            <path
              d="M28 4 L13 13 L19 16 L21 22 L28 4 Z"
              fill="currentColor"
            />
            <path
              d="M19 16 L21 22 L23 14 Z"
              fill="currentColor"
              opacity="0.7"
            />
          </svg>
        </span>
        <span className="sidebar-brand-name">journify</span>
      </div>

      <nav className="sidebar-nav">
        <NavItem
          icon={<HomeIcon />} label="Home"
          active={active === 'home'} onClick={() => onSelect('home')}
        />
        <NavItem
          icon={<TableIcon />} label="Table view"
          active={active === 'table'} onClick={() => onSelect('table')}
        />

        <div className="sidebar-sep" />

        <NavItem
          icon={<InboxIcon />} label="Approval queue" count={queueCount}
          active={active === 'queue'} onClick={() => onSelect('queue')}
        />
        <NavItem
          icon={<SendIcon />} label="Pipeline" count={pipelineCount}
          active={active === 'pipeline'} onClick={() => onSelect('pipeline')}
        />
        <NavItem
          icon={<CheckIcon />} label="Sent" count={sentCount}
          active={active === 'sent'} onClick={() => onSelect('sent')}
        />
        <NavItem
          icon={<ArchiveIcon />} label="Archived" count={archivedCount}
          active={active === 'archived'} onClick={() => onSelect('archived')}
        />

        <div className="sidebar-sep" />

        <NavItem
          icon={<SignalIcon />} label="Unmatched signals"
          badge={unmatchedCount > 0 ? { text: String(unmatchedCount), tone: 'warn' } : undefined}
          active={active === 'unmatched'} onClick={() => onSelect('unmatched')}
        />
        <NavItem
          icon={<ImportIcon />} label="Import data"
          active={active === 'import'} onClick={() => onSelect('import')}
        />
        <NavItem
          icon={<SearchIcon />} label="Source new leads"
          badge={{ text: 'New', tone: 'info' }}
          onClick={onOpenSourcing}
        />
      </nav>

      <div className="sidebar-bottom">
        <NavItem icon={<SettingsIcon />} label="Settings" disabled />
        <NavItem icon={<BookIcon />} label="Docs" disabled />
      </div>
    </aside>
  )
}

function NavItem({
  icon, label, count, badge, active, onClick, disabled,
}: {
  icon: ReactNode
  label: string
  count?: number
  badge?: { text: string; tone: 'warn' | 'info' }
  active?: boolean
  onClick?: () => void
  disabled?: boolean
}) {
  return (
    <button
      className={`sidebar-item ${active ? 'on' : ''}`}
      onClick={onClick}
      disabled={disabled}
      title={disabled ? 'Coming soon' : undefined}
    >
      <span className="sidebar-item-icon">{icon}</span>
      <span className="sidebar-item-label">{label}</span>
      {count !== undefined && count > 0 && <span className="sidebar-item-cnt">{count}</span>}
      {badge && <span className={`sidebar-item-badge ${badge.tone}`}>{badge.text}</span>}
    </button>
  )
}

/* ─── Icons ────────────────────────────────────────────── */

function _Svg({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  )
}

function HomeIcon() { return <_Svg><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></_Svg> }
function TableIcon() { return <_Svg><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="3" x2="9" y2="21"/></_Svg> }
function InboxIcon() { return <_Svg><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></_Svg> }
function SendIcon() { return <_Svg><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></_Svg> }
function CheckIcon() { return <_Svg><polyline points="20 6 9 17 4 12"/></_Svg> }
function ArchiveIcon() { return <_Svg><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></_Svg> }
function SignalIcon() { return <_Svg><path d="M2 12h2a8 8 0 0 1 16 0h2"/><path d="M6 12h2a4 4 0 0 1 8 0h2"/><circle cx="12" cy="12" r="1.5"/></_Svg> }
function ImportIcon() { return <_Svg><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></_Svg> }
function SearchIcon() { return <_Svg><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></_Svg> }
function SettingsIcon() { return <_Svg><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></_Svg> }
function BookIcon() { return <_Svg><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></_Svg> }
