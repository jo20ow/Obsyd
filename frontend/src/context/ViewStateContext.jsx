import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import useZones from '../hooks/useZones'
import { RANGE_KEYS, DEFAULT_RANGE } from '../utils/ranges'

// ViewStateContext — the desk's navigation spine. Holds the one selected bidding
// ZONE and the one date RANGE that every zone-/range-aware surface reads, so the
// user picks each ONCE (region-first, like gridstatus.io) instead of re-setting it
// per panel. Mirrors both into the URL query (?zone=&range=) — the tab stays in the
// #hash (owned by App.jsx) — so a view is shareable/bookmarkable, and persists to
// localStorage so a return visit restores it. Follows the ModeContext pattern.

const ViewStateContext = createContext()

function readInitial() {
  let urlZone = null
  let urlRange = null
  try {
    const p = new URLSearchParams(window.location.search)
    urlZone = p.get('zone')
    const r = p.get('range')
    if (r && RANGE_KEYS.includes(r)) urlRange = r
  } catch { /* no window / bad URL */ }

  let lsZone = null
  let lsRange = null
  try {
    lsZone = localStorage.getItem('obsyd-zone')
    const r = localStorage.getItem('obsyd-range')
    if (r && RANGE_KEYS.includes(r)) lsRange = r
  } catch { /* storage blocked */ }

  return {
    // null zone → fall back to the server default (resolved during render below).
    zone: urlZone || lsZone || null,
    range: urlRange || lsRange || DEFAULT_RANGE,
  }
}

export function ViewStateProvider({ children }) {
  const { defaultZone } = useZones()
  const initial = readInitial()
  const [zone, setZoneState] = useState(initial.zone) // raw choice; null until picked
  const [range, setRangeState] = useState(initial.range)

  // Effective zone is derived during render (no setState-in-effect): an explicit
  // url/localStorage/user choice wins; otherwise the async server default; else DE_LU.
  const effectiveZone = zone || defaultZone || 'DE_LU'

  const setZone = useCallback((z) => {
    setZoneState(z)
    try { localStorage.setItem('obsyd-zone', z) } catch { /* ignore */ }
  }, [])

  const setRange = useCallback((r) => {
    setRangeState(r)
    try { localStorage.setItem('obsyd-range', r) } catch { /* ignore */ }
  }, [])

  // Mirror effective zone + range into the URL query, preserving pathname + #tab.
  // replaceState (not pushState) so it doesn't spam browser history on every toggle.
  useEffect(() => {
    try {
      const p = new URLSearchParams(window.location.search)
      p.set('zone', effectiveZone)
      p.set('range', range)
      const qs = p.toString()
      const url = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash
      window.history.replaceState(null, '', url)
    } catch { /* ignore */ }
  }, [effectiveZone, range])

  const value = { zone: effectiveZone, range, setZone, setRange }
  return <ViewStateContext.Provider value={value}>{children}</ViewStateContext.Provider>
}

export function useViewState() {
  return useContext(ViewStateContext)
}
