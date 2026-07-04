import { useEffect, useMemo, useRef, useState } from 'react'
import useZones from '../hooks/useZones'

const API = '/api'

// Subsequence fuzzy match: every char of `q` appears in order in `text`.
// Returns a score (lower = better: earlier + tighter match) or null if no match.
function fuzzyScore(text, q) {
  if (!q) return 0
  const t = text.toLowerCase()
  let ti = 0
  let score = 0
  let last = -1
  for (const ch of q.toLowerCase()) {
    const idx = t.indexOf(ch, ti)
    if (idx === -1) return null
    score += idx - last - 1 // gap penalty
    if (last === -1) score += idx // prefix penalty (earlier start wins)
    last = idx
    ti = idx + 1
  }
  return score
}

/**
 * ⌘K command palette — the terminal command line. Navigate to any view/zone,
 * add a cross-asset instrument to the watchlist, or open settings, by typing.
 * Mounted only while open (fresh state each time); overlay mirrors the
 * SettingsPanel backdrop+panel pattern (no portal).
 */
export default function CommandPalette({ onClose, tabs, setActiveTab, setEnergyZone, openSettings, authed }) {
  const { zones } = useZones()
  const [query, setQuery] = useState('')
  const [sel, setSel] = useState(0)
  const [catalog, setCatalog] = useState([]) // [{kind,key,label}]
  const inputRef = useRef(null)

  // Focus input on mount.
  useEffect(() => {
    const id = setTimeout(() => inputRef.current?.focus(), 0)
    return () => clearTimeout(id)
  }, [])

  // Load the watchlist catalog once (for "add to watchlist" commands), authed only.
  useEffect(() => {
    if (!authed) return
    let alive = true
    fetch(`${API}/watchlist/catalog`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive || !data) return
        const flat = []
        for (const [kind, items] of Object.entries(data)) {
          for (const it of items) flat.push({ kind, key: it.key, label: it.label })
        }
        setCatalog(flat)
      })
      .catch(() => {})
    return () => { alive = false }
  }, [authed])

  const commands = useMemo(() => {
    const cmds = []
    for (const t of tabs) {
      cmds.push({ id: `tab:${t.key}`, label: `Go to ${t.label}`, hint: 'view', run: () => setActiveTab(t.key) })
    }
    for (const z of zones) {
      cmds.push({
        id: `zone:${z.key}`,
        label: `Power zone: ${z.label}`,
        hint: 'zone',
        run: () => { setEnergyZone(z.key); setActiveTab('energy') },
      })
    }
    if (openSettings) cmds.push({ id: 'act:settings', label: 'Open settings', hint: 'action', run: () => openSettings() })
    cmds.push({ id: 'act:watchlist', label: 'Manage watchlist & alerts', hint: 'action', run: () => setActiveTab('alerts') })
    if (authed) {
      for (const c of catalog) {
        cmds.push({
          id: `watch:${c.kind}:${c.key}`,
          label: `Watchlist: add ${c.label}`,
          hint: c.kind,
          run: () => {
            fetch(`${API}/watchlist`, {
              method: 'POST',
              credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ kind: c.kind, key: c.key }),
            }).catch(() => {})
          },
        })
      }
    }
    return cmds
  }, [tabs, zones, catalog, authed, setActiveTab, setEnergyZone, openSettings])

  const results = useMemo(() => {
    const scored = []
    for (const c of commands) {
      const s = fuzzyScore(c.label, query)
      if (s !== null) scored.push({ c, s })
    }
    scored.sort((a, b) => a.s - b.s)
    return scored.slice(0, 40).map((x) => x.c)
  }, [commands, query])

  const exec = (cmd) => {
    if (!cmd) return
    cmd.run()
    onClose()
  }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); exec(results[sel]) }
  }

  const safeSel = Math.min(sel, Math.max(results.length - 1, 0))

  return (
    <div className="fixed inset-0 bg-black/60 z-40 flex items-start justify-center pt-[12vh] px-4" onClick={onClose}>
      <div
        className="w-full max-w-lg border border-border bg-surface rounded shadow-xl shadow-black/50 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setSel(0) }}
          onKeyDown={onKeyDown}
          placeholder="Search commands — view, zone, watchlist…"
          className="w-full bg-transparent border-b border-border px-4 py-3 font-mono text-sm text-neutral-200 placeholder:text-neutral-600 outline-none"
        />
        <div className="max-h-[50vh] overflow-y-auto scrollbar-hidden">
          {results.length === 0 && (
            <div className="px-4 py-4 font-mono text-[11px] text-neutral-600">No matching command.</div>
          )}
          {results.map((c, i) => (
            <button
              key={c.id}
              onMouseEnter={() => setSel(i)}
              onClick={() => exec(c)}
              className={`w-full flex items-center justify-between gap-3 px-4 py-2 text-left font-mono text-[12px] ${
                i === safeSel ? 'bg-cyan-glow/10 text-cyan-glow' : 'text-neutral-300 hover:bg-white/[0.03]'
              }`}
            >
              <span className="truncate">{c.label}</span>
              <span className="text-[9px] text-neutral-600 uppercase tracking-wider shrink-0">{c.hint}</span>
            </button>
          ))}
        </div>
        <div className="px-4 py-1.5 border-t border-border font-mono text-[9px] text-neutral-600 flex gap-3">
          <span>↑↓ navigate</span><span>↵ run</span><span>esc close</span>
        </div>
      </div>
    </div>
  )
}
