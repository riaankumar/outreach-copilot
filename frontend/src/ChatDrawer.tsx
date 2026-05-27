import { useEffect, useRef, useState } from 'react'
import { apiUrl } from './api'

type UIMessage = {
  role: 'user' | 'assistant'
  text: string
  tool_calls?: { name: string; is_write: boolean; needs_confirmation: boolean }[]
}

type ApiMessage = { role: string; content: any }

type Props = {
  open: boolean
  onClose: () => void
  onActionTaken: () => void  // refresh dashboard after a write
}

const STARTER_PROMPTS = [
  'How am I doing this week?',
  'Show me my highest fit districts',
  'What pending districts have signals?',
  'Brief Larkspur USD',
]

export default function ChatDrawer({ open, onClose, onActionTaken }: Props) {
  const [uiMessages, setUiMessages] = useState<UIMessage[]>([])
  const [apiMessages, setApiMessages] = useState<ApiMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open && uiMessages.length === 0) {
      setUiMessages([{
        role: 'assistant',
        text: "Hi — I can answer questions about your pipeline and run dashboard actions for you. I'll always confirm before approving, editing, or rejecting anything. Try one of the prompts below or just ask.",
      }])
    }
  }, [open])

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [uiMessages, sending])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && open) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  async function send(text: string) {
    if (!text.trim() || sending) return

    const userMsg: UIMessage = { role: 'user', text }
    setUiMessages((m) => [...m, userMsg])
    setInput('')
    setSending(true)

    const nextApi: ApiMessage[] = [...apiMessages, { role: 'user', content: text }]

    try {
      const res = await fetch(apiUrl('/api/chat'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ messages: nextApi }),
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const data = await res.json()
      setUiMessages((m) => [...m, {
        role: 'assistant',
        text: data.reply,
        tool_calls: data.tool_calls,
      }])
      setApiMessages(data.messages)
      if (data.tool_calls?.some((t: any) => t.is_write && t.result?.ok)) {
        onActionTaken()
      }
    } catch (e: any) {
      setUiMessages((m) => [...m, {
        role: 'assistant',
        text: `Error: ${e.message ?? e}`,
      }])
    } finally {
      setSending(false)
    }
  }

  function resetConversation() {
    setUiMessages([])
    setApiMessages([])
  }

  if (!open) return null

  return (
    <>
      <div className="chat-bg" onClick={onClose} />
      <aside className="chat-drawer" role="dialog" aria-label="SDR copilot">
        <header className="chat-hdr">
          <div>
            <div className="chat-eyebrow">Copilot</div>
            <h3>Ask anything</h3>
          </div>
          <div className="chat-hdr-actions">
            <button className="ghost sm" onClick={resetConversation} disabled={uiMessages.length === 0}>
              New chat
            </button>
            <button className="ghost sm" onClick={onClose} aria-label="Close chat">
              Close <span className="kbd">Esc</span>
            </button>
          </div>
        </header>

        <div className="chat-body" ref={scrollRef}>
          {uiMessages.map((m, i) => (
            <Message key={i} m={m} />
          ))}
          {sending && (
            <div className="msg-row msg-assistant">
              <div className="msg-bubble msg-thinking">
                <span className="dot-dot"><i /><i /><i /></span>
              </div>
            </div>
          )}

          {uiMessages.length <= 1 && !sending && (
            <div className="starters">
              {STARTER_PROMPTS.map((p) => (
                <button key={p} className="starter" onClick={() => send(p)}>
                  {p}
                </button>
              ))}
            </div>
          )}
        </div>

        <form
          className="chat-input"
          onSubmit={(e) => { e.preventDefault(); send(input) }}
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send(input)
              }
            }}
            placeholder="Ask about your pipeline, or 'approve Salt Lake'…"
            rows={2}
            disabled={sending}
          />
          <button type="submit" className="accent" disabled={!input.trim() || sending}>
            Send
          </button>
        </form>
      </aside>
    </>
  )
}

function Message({ m }: { m: UIMessage }) {
  return (
    <div className={`msg-row msg-${m.role}`}>
      <div className="msg-bubble">
        <MessageBody text={m.text} />
        {m.tool_calls && m.tool_calls.length > 0 && (
          <div className="tool-trace">
            {m.tool_calls.map((t, i) => (
              <span key={i} className={`tool-pill ${t.is_write ? 'tool-pill-write' : ''} ${t.needs_confirmation ? 'tool-pill-pending' : ''}`}>
                {prettyTool(t.name)}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function prettyTool(name: string): string {
  return ({
    list_districts: 'Listed districts',
    get_district: 'Read district',
    summarize_pipeline: 'Summarized pipeline',
    run_pipeline: 'Ran pipeline',
    approve_draft: 'Approved draft',
    edit_draft: 'Edited draft',
    reject_draft: 'Rejected draft',
  } as Record<string, string>)[name] ?? name
}

/* Minimal Markdown rendering — paragraphs, bold (**), inline code (`),
   tables (|...|), and bullet lists. Kept tiny on purpose. */
function MessageBody({ text }: { text: string }) {
  const blocks = parseBlocks(text)
  return (
    <>
      {blocks.map((b, i) => {
        if (b.type === 'table') return <MdTable key={i} rows={b.rows} />
        if (b.type === 'list') return (
          <ul key={i}>
            {b.items.map((it, j) => <li key={j} dangerouslySetInnerHTML={{ __html: inline(it) }} />)}
          </ul>
        )
        if (b.type === 'heading') return <h4 key={i} dangerouslySetInnerHTML={{ __html: inline(b.text) }} />
        return <p key={i} dangerouslySetInnerHTML={{ __html: inline(b.text) }} />
      })}
    </>
  )
}

type Block =
  | { type: 'paragraph'; text: string }
  | { type: 'heading'; text: string }
  | { type: 'list'; items: string[] }
  | { type: 'table'; rows: string[][] }

function parseBlocks(text: string): Block[] {
  const lines = text.split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }

    // Table — lines starting with |, second line is the separator
    if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        if (/^\s*\|[\s\-:|]+\|\s*$/.test(lines[i])) { i++; continue }
        rows.push(lines[i].trim().slice(1, -1).split('|').map(c => c.trim()))
        i++
      }
      blocks.push({ type: 'table', rows })
      continue
    }

    // List
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''))
        i++
      }
      blocks.push({ type: 'list', items })
      continue
    }

    // Heading (## or **bold-only line**)
    if (line.startsWith('## ')) {
      blocks.push({ type: 'heading', text: line.slice(3) })
      i++
      continue
    }

    // Paragraph
    const buf: string[] = []
    while (i < lines.length && lines[i].trim() && !lines[i].trim().startsWith('|') && !/^\s*[-*]\s+/.test(lines[i])) {
      buf.push(lines[i])
      i++
    }
    blocks.push({ type: 'paragraph', text: buf.join(' ') })
  }
  return blocks
}

function inline(s: string): string {
  // escape HTML
  s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  // bold
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  // inline code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
  return s
}

function MdTable({ rows }: { rows: string[][] }) {
  if (!rows.length) return null
  const [head, ...body] = rows
  return (
    <table className="md-table">
      <thead>
        <tr>{head.map((c, i) => <th key={i} dangerouslySetInnerHTML={{ __html: inline(c) }} />)}</tr>
      </thead>
      <tbody>
        {body.map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j} dangerouslySetInnerHTML={{ __html: inline(c) }} />)}</tr>
        ))}
      </tbody>
    </table>
  )
}
