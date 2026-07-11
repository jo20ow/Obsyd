import { useState, useEffect, useRef } from 'react'

import FreshnessCaption from './FreshnessCaption'

export function InfoPopover({ text }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div className="relative inline-flex" ref={ref}>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
        className="w-3.5 h-3.5 rounded-full border border-neutral-700/60 text-neutral-600 hover:text-neutral-400 hover:border-neutral-500 inline-flex items-center justify-center text-[8px] font-mono leading-none transition-colors shrink-0"
        title="Info"
      >
        i
      </button>
      {open && (
        <div className="absolute top-5 left-0 z-50 w-64 max-w-[calc(100vw-2rem)] border border-border bg-surface rounded px-3 py-2.5 font-mono text-[10px] text-neutral-400 leading-relaxed shadow-xl shadow-black/20">
          {text}
        </div>
      )}
    </div>
  )
}

export default function Panel({ id, title, info, collapsible = false, defaultCollapsed = false, headerRight, downloadUrl, freshness, children }) {
  const [collapsed, setCollapsed] = useState(() => {
    if (!collapsible) return false
    try {
      const saved = localStorage.getItem(`obsyd-panel-${id}`)
      if (saved === null) return defaultCollapsed  // first visit → honour the panel's default
      return saved === '1'
    } catch {
      return defaultCollapsed
    }
  })

  useEffect(() => {
    if (!collapsible) return
    try {
      localStorage.setItem(`obsyd-panel-${id}`, collapsed ? '1' : '0')
    } catch { /* localStorage unavailable */ }
  }, [collapsed, id, collapsible])

  return (
    <div id={id ? `panel-${id}` : undefined} className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div
        className={`flex items-center justify-between px-4 py-2.5 ${
          !collapsed ? 'border-b border-border/50' : ''
        }`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[11px] font-semibold text-neutral-300 truncate">
            {title}
          </span>
          {info && <InfoPopover text={info} />}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {freshness && <FreshnessCaption meta={freshness} />}
          {downloadUrl && (
            <a
              href={downloadUrl}
              onClick={(e) => e.stopPropagation()}
              className="font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
              title="Download this panel's data as CSV"
            >
              ↓ CSV
            </a>
          )}
          {headerRight}
          {collapsible && (
            <button
              onClick={() => setCollapsed(!collapsed)}
              className="font-mono text-neutral-600 hover:text-neutral-400 text-[11px] transition-colors w-5 h-5 flex items-center justify-center rounded hover:bg-white/5"
              title={collapsed ? 'Expand' : 'Collapse'}
            >
              {collapsed ? '▸' : '▾'}
            </button>
          )}
        </div>
      </div>
      {!collapsed && children}
    </div>
  )
}
