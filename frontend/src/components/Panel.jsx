import { useState, useEffect, useRef } from 'react'

import FreshnessCaption from './FreshnessCaption'
import Provenance from './Provenance'

export function InfoPopover({ text, wide = false }) {
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
        <div className={`absolute top-5 left-0 z-50 ${wide ? 'w-96' : 'w-64'} max-w-[calc(100vw-2rem)] max-h-[60vh] overflow-y-auto border border-border bg-surface rounded px-3 py-2.5 font-mono text-[10px] text-neutral-400 leading-relaxed shadow-xl shadow-black/20`}>
          {text}
        </div>
      )}
    </div>
  )
}

const chipCls = (active) =>
  `font-code text-[9px] tracking-wider border rounded px-1.5 py-0.5 transition-colors ${
    active
      ? 'text-cyan-glow border-cyan-glow/40'
      : 'text-neutral-500 border-border hover:text-cyan-glow hover:border-cyan-glow/40'
  }`

function _jsonUrl(url) {
  const u = new URL(url, window.location.origin)
  u.searchParams.delete('format')
  return u
}

// "API" chip: copies the panel's underlying JSON API URL — the CSV downloadUrl
// minus its format param (or a JSON-only apiUrl verbatim), absolutized against
// the current origin so it stays correct on a self-hosted instance alike.
function ApiChip({ url }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)

  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = (e) => {
    e.stopPropagation()
    let apiUrl
    try {
      apiUrl = _jsonUrl(url).toString()
    } catch {
      return
    }
    navigator.clipboard?.writeText(apiUrl).then(() => {
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }

  return (
    <button onClick={copy} className={chipCls(copied)} title="Copy this panel's JSON API URL">
      {copied ? '✓ copied' : 'API'}
    </button>
  )
}

// "PY" chip: copies a ready-to-run Python snippet for the panel's data — the
// obsyd client for /api/v1/series URLs, plain requests for everything else
// (the survey's "copy as code" exit ramp on every chart).
function PyChip({ url }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = (e) => {
    e.stopPropagation()
    let snippet
    try {
      const u = _jsonUrl(url)
      const p = u.searchParams
      if (u.pathname === '/api/v1/series' && p.get('series') && p.get('zone')) {
        const start = p.get('start') ? `, start="${p.get('start')}"` : ''
        snippet = `from obsyd import Obsyd\ndf = Obsyd().series("${p.get('series')}", "${p.get('zone')}"${start})`
      } else {
        snippet = `import requests\ndata = requests.get("${u.toString()}").json()`
      }
    } catch {
      return
    }
    navigator.clipboard?.writeText(snippet).then(() => {
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }

  return (
    <button onClick={copy} className={chipCls(copied)} title="Copy a Python snippet for this panel's data">
      {copied ? '✓ copied' : 'PY'}
    </button>
  )
}

// "↓ PNG" — export the panel's largest chart SVG as a 2x PNG with the theme
// background and a small obsyd.dev attribution (the Energy-Charts share-image
// pattern). Renders nothing useful for table-only panels: the click simply
// finds no svg and flashes a dash.
function PngChip({ containerRef, filename }) {
  const [state, setState] = useState(null) // null | 'ok' | 'none'
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])

  const flash = (s) => {
    setState(s)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setState(null), 1500)
  }

  const exportPng = (e) => {
    e.stopPropagation()
    const root = containerRef.current
    const svgs = root ? [...root.querySelectorAll('svg')] : []
    if (!svgs.length) { flash('none'); return }
    const svg = svgs.reduce((a, b) =>
      (b.clientWidth * b.clientHeight > a.clientWidth * a.clientHeight ? b : a))
    const w = svg.clientWidth || 600
    const h = svg.clientHeight || 300
    const clone = svg.cloneNode(true)
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    const img = new Image()
    img.onload = () => {
      const scale = 2
      const canvas = document.createElement('canvas')
      canvas.width = w * scale
      canvas.height = (h + 14) * scale
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = getComputedStyle(root).backgroundColor || '#0f1115'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.scale(scale, scale)
      ctx.drawImage(img, 0, 0, w, h)
      ctx.font = '9px ui-monospace, monospace'
      ctx.fillStyle = '#8b8fa3'
      ctx.fillText('obsyd.dev', w - 52, h + 9)
      const a = document.createElement('a')
      a.download = filename
      a.href = canvas.toDataURL('image/png')
      a.click()
      flash('ok')
    }
    img.onerror = () => flash('none')
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(clone))
  }

  return (
    <button onClick={exportPng} className={chipCls(state === 'ok')}
      title="Download this panel's chart as a PNG">
      {state === 'ok' ? '✓ saved' : state === 'none' ? '–' : '↓ PNG'}
    </button>
  )
}

export default function Panel({ id, title, info, infoWide = false, collapsible = false, defaultCollapsed = false, expandSignal, headerRight, downloadUrl, apiUrl, freshness, source, children }) {
  const bodyRef = useRef(null)
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

  // External expand signal (e.g. a map click focusing this panel): any NEW
  // non-null value un-collapses. It only ever expands — never re-collapses.
  // Adjusted during render (React's "storing information from previous renders"
  // pattern) instead of an effect, so the panel never paints collapsed first.
  const [seenSignal, setSeenSignal] = useState(expandSignal)
  if (expandSignal !== seenSignal) {
    setSeenSignal(expandSignal)
    if (expandSignal != null && collapsed) setCollapsed(false)
  }

  return (
    <div id={id ? `panel-${id}` : undefined} className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      {/* flex-wrap + ml-auto: on a narrow panel the chip row wraps to a second
          line instead of truncating the title to "EU …" (QA walkthrough). */}
      <div
        className={`flex flex-wrap items-center justify-between gap-y-1 px-4 py-2.5 ${
          !collapsed ? 'border-b border-border/50' : ''
        }`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[12px] font-medium smallcaps text-neutral-400 truncate">
            {title}
          </span>
          {info && <InfoPopover text={info} wide={infoWide} />}
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-auto">
          {freshness && <FreshnessCaption meta={freshness} />}
          {downloadUrl && (
            <a
              href={downloadUrl}
              onClick={(e) => e.stopPropagation()}
              className="font-code text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
              title="Download this panel's data as CSV (the same URL serves JSON or Parquet via format=)"
            >
              ↓ CSV
            </a>
          )}
          {/* downloadUrl = a real CSV exists; apiUrl = JSON-only desk endpoint.
              Either way the chip row gives every data panel its exit ramps
              (the survey's rule: every chart converts viewers to API users). */}
          {(downloadUrl || apiUrl) && (
            <>
              <ApiChip url={downloadUrl || apiUrl} />
              <PyChip url={downloadUrl || apiUrl} />
              <PngChip containerRef={bodyRef} filename={`obsyd_${id || 'chart'}.png`} />
            </>
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
      {!collapsed && <div ref={bodyRef}>{children}</div>}
      {/* Visible provenance line (was buried in ⓘ popovers) — pass the exact
          source claim, e.g. "ENTSO-E A44 · day-ahead auction". */}
      {!collapsed && source && (
        <Provenance source={source} className="px-4 py-1.5 border-t border-border/40" />
      )}
    </div>
  )
}
