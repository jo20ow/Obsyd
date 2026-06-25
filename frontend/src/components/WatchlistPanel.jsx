import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'

const API = '/api'

/**
 * Per-user watchlist (Pro). Pick the materials/zones OBSYD should watch for
 * you — they drive the personalised daily brief and surface in the supply
 * feed. Chip toggles: click to add, click again to remove. Catalog is public;
 * the user's own items are Pro-gated (this panel renders inside a Pro section).
 */
export default function WatchlistPanel() {
  const { isPro, openPricing } = useAuth()
  const [catalog, setCatalog] = useState({ material: [], zone: [] })
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    setError(null)
    Promise.all([
      fetch(`${API}/watchlist/catalog`).then((r) => (r.ok ? r.json() : { material: [], zone: [] })),
      fetch(`${API}/watchlist`, { credentials: 'include' }).then((r) =>
        r.ok ? r.json() : { items: [] }
      ),
    ])
      .then(([cat, mine]) => {
        setCatalog(cat)
        setItems(mine.items || [])
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  if (!isPro) {
    return (
      <div className="border border-border bg-surface rounded p-6 text-center font-mono">
        <div className="text-[10px] tracking-wider text-cyan-glow mb-2">YOUR WATCHLIST</div>
        <div className="text-[12px] text-neutral-400 mb-4 max-w-md mx-auto leading-relaxed">
          Pick the materials and zones you care about — OBSYD watches them for you and folds them
          into your alerts and daily brief. A Pro feature.
        </div>
        <button
          type="button"
          onClick={openPricing}
          className="px-5 py-2 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors font-semibold"
        >
          Upgrade to Pro →
        </button>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="border border-border bg-surface rounded p-6 font-mono text-[10px] text-neutral-600 animate-pulse text-center">
        WATCHLIST // LOADING ...
      </div>
    )
  }

  const watchedKeys = new Set(items.map((i) => `${i.kind}:${i.key}`))

  const add = async (kind, key) => {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${API}/watchlist`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, key }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id) => {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${API}/watchlist/${id}`, { method: 'DELETE', credentials: 'include' })
      if (!res.ok) throw new Error('remove failed')
      refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const renderChips = (kind) =>
    (catalog[kind] || []).map((c) => {
      const on = watchedKeys.has(`${kind}:${c.key}`)
      const mine = items.find((i) => i.kind === kind && i.key === c.key)
      return (
        <button
          key={c.key}
          type="button"
          disabled={busy}
          onClick={() => (on ? remove(mine.id) : add(kind, c.key))}
          className={`font-mono text-[10px] px-2 py-1 rounded border transition-colors disabled:opacity-50 ${
            on
              ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10'
              : 'text-neutral-400 border-border hover:border-cyan-glow/40'
          }`}
        >
          {on ? '✓ ' : '+ '}
          {c.label}
        </button>
      )
    })

  return (
    <div className="space-y-3">
      {error && (
        <div className="border border-red-500/30 bg-red-500/5 px-4 py-2 text-[11px] text-red-300 font-mono">
          {error}
        </div>
      )}
      <div className="border border-border bg-surface rounded p-4 space-y-3">
        <div className="font-mono text-[10px] text-neutral-500 tracking-wider">
          YOUR WATCHLIST · {items.length} watched
        </div>
        <div>
          <div className="font-mono text-[9px] text-neutral-600 mb-1.5">MATERIALS</div>
          <div className="flex flex-wrap gap-1.5">{renderChips('material')}</div>
        </div>
        <div>
          <div className="font-mono text-[9px] text-neutral-600 mb-1.5">ZONES</div>
          <div className="flex flex-wrap gap-1.5">{renderChips('zone')}</div>
        </div>
        <div className="font-mono text-[9px] text-neutral-700 leading-relaxed">
          Watched items drive your daily brief and the supply-disruption feed. Add alert rules below
          to get emailed the moment one of them deviates.
        </div>
      </div>
    </div>
  )
}
