import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'

const API = '/api'
const POWER_ZONES = new Set(['DE_LU', 'FR', 'NL'])

// Which view a watchlist chip jumps to when clicked.
function navFor(item, setActiveTab, setEnergyZone) {
  if (item.kind === 'symbol') return () => setActiveTab('market')
  if (item.kind === 'crypto') return () => setActiveTab('crypto')
  if (item.kind === 'material') return () => setActiveTab('critical')
  if (item.kind === 'zone') {
    if (POWER_ZONES.has(item.key)) return () => { setEnergyZone(item.key); setActiveTab('energy') }
    return () => setActiveTab('overview') // chokepoint geofences live on OVERVIEW
  }
  return () => {}
}

/**
 * Always-on terminal strip: a ⌘K command trigger + the user's cross-asset
 * watchlist (power zones, gas/commodity symbols, materials, chokepoints) as
 * clickable chips that jump to the relevant view. Login-gated read.
 */
export default function TerminalBar({ onOpenPalette, setActiveTab, setEnergyZone, refreshKey = 0 }) {
  const { user } = useAuth()
  const authed = !!user?.authenticated
  const [items, setItems] = useState([])

  useEffect(() => {
    if (!authed) return
    let alive = true
    fetch(`${API}/watchlist`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d?.items) setItems(d.items) })
      .catch(() => {})
    return () => { alive = false }
  }, [authed, refreshKey])

  const shown = authed ? items : [] // never render stale items after logout

  const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform || '')

  return (
    <div className="border border-border bg-surface rounded px-3 py-1.5 flex items-center gap-3 overflow-hidden">
      <button
        onClick={onOpenPalette}
        className="flex items-center gap-2 font-mono text-[10px] text-neutral-400 hover:text-cyan-glow shrink-0"
        title="Command palette"
      >
        <span className="border border-border rounded px-1.5 py-0.5 text-neutral-500">{isMac ? '⌘' : 'Ctrl'} K</span>
        <span className="hidden sm:inline">Search / commands</span>
      </button>

      <div className="w-px h-4 bg-border shrink-0" />

      <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-hidden min-w-0">
        {!authed && (
          <span className="font-mono text-[10px] text-neutral-600">Log in to build a cross-asset watchlist</span>
        )}
        {authed && shown.length === 0 && (
          <span className="font-mono text-[10px] text-neutral-600">
            Watchlist empty — add via {isMac ? '⌘' : 'Ctrl'}K
          </span>
        )}
        {shown.map((it) => (
          <button
            key={it.id}
            onClick={navFor(it, setActiveTab, setEnergyZone)}
            className="font-mono text-[10px] text-neutral-300 border border-border rounded px-2 py-0.5 hover:border-cyan-glow/40 hover:text-cyan-glow shrink-0"
            title={`${it.kind} · ${it.key}`}
          >
            {it.label}
          </button>
        ))}
      </div>
    </div>
  )
}
