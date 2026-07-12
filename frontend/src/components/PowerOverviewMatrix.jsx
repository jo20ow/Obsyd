import { useMemo, useState } from 'react'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'

// Single-glance overview — read all bidding zones at once, colour-first, like
// Electricity Maps / Grid Status. Colour encodes how far each metric sits from its
// own ~90-day norm, so the European power picture reads in one second. Click a zone
// to drill into its detail; click a column header to sort. Descriptive, not a forecast.
const API = '/api'

const STATE = {
  CALM: { t: 'text-green-glow', d: 'bg-green-glow' },
  ELEVATED: { t: 'text-yellow-400', d: 'bg-yellow-400' },
  STRESSED: { t: 'text-red-400', d: 'bg-red-400' },
}
const STATE_ORDER = { CALM: 0, ELEVATED: 1, STRESSED: 2 }

const zColor = (z) =>
  z == null ? 'text-neutral-400' : Math.abs(z) >= 3 ? 'text-red-400' : Math.abs(z) >= 2 ? 'text-yellow-400' : 'text-neutral-300'

const COLUMNS = [
  { key: 'zone', label: 'Zone', align: 'left', get: (z) => z.zone_label || z.zone },
  { key: 'state', label: 'State', align: 'left', get: (z) => STATE_ORDER[z.state] ?? -1 },
  { key: 'price', label: 'Day-ahead', align: 'right', get: (z) => z.price_close },
  { key: 'residual', label: 'Residual', align: 'right', get: (z) => z.residual_gw },
  { key: 'renewables', label: 'Renewables', align: 'right', get: (z) => (z.renewable_reliable === false ? null : z.renewable_share) },
]

export default function PowerOverviewMatrix({ selectedZone, onSelect }) {
  const { data, loading, error } = useFetchWithError(`${API}/power/overview`, { pollMs: POLL_FAST_MS })
  const [sort, setSort] = useState({ key: 'zone', dir: 'asc' })

  const sorted = useMemo(() => {
    const rows = data?.zones ? [...data.zones] : []
    const get = COLUMNS.find((c) => c.key === sort.key)?.get || (() => null)
    rows.sort((a, b) => {
      const av = get(a)
      const bv = get(b)
      if (av == null && bv == null) return 0
      if (av == null) return 1  // nulls last
      if (bv == null) return -1
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sort.dir === 'asc' ? cmp : -cmp
    })
    return rows
  }, [data, sort])

  // The all-zones matrix is the default tab's core — it must never be a silent
  // hole. Loading keeps a quiet placeholder; a fetch error or empty backend
  // says so instead of rendering nothing.
  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EUROPEAN POWER · ALL ZONES // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && loading)
    return (
      <div className="border border-border bg-surface rounded px-4 py-4">
        <div className="font-mono text-[10px] text-neutral-600 animate-pulse">Loading all zones…</div>
      </div>
    )
  if (!data?.available)
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          EUROPEAN POWER · ALL ZONES — no zone data yet; check back shortly.
        </div>
      </div>
    )

  const toggle = (key) => setSort((s) => ({ key, dir: s.key === key && s.dir === 'asc' ? 'desc' : 'asc' }))
  const arrow = (key) => (sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '')

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="px-4 py-2.5 border-b border-border/60 flex items-center gap-2">
        <span className="font-mono text-[12px] font-semibold text-neutral-300">European power · all zones</span>
        <span className="font-mono text-[9px] text-neutral-700 ml-auto">sort ↕ · click a zone for detail →</span>
      </div>
      <div className="overflow-x-auto max-h-[520px] overflow-y-auto">
        <table className="w-full font-mono text-[11px]">
          <thead className="sticky top-0 bg-surface">
            <tr className="text-[9px] text-neutral-500">
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => toggle(c.key)}
                  className={`${c.align === 'right' ? 'text-right' : 'text-left'} ${c.key === 'zone' || c.key === 'renewables' ? 'px-3' : 'px-2'} py-1 font-normal cursor-pointer hover:text-neutral-300 select-none`}
                >
                  {c.label}{arrow(c.key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((z) => {
              const st = STATE[z.state] || STATE.CALM
              const sel = z.zone === selectedZone
              return (
                <tr
                  key={z.zone}
                  onClick={() => onSelect?.(z.zone)}
                  className={`cursor-pointer border-t border-border/40 hover:bg-white/[0.03] ${sel ? 'bg-cyan-glow/5' : ''}`}
                >
                  <td className="px-3 py-2 text-neutral-200 whitespace-nowrap">
                    {z.zone_label}
                    {sel && <span className="text-cyan-glow"> ‹</span>}
                    {z.stale && <span className="text-orange-400/70 text-[8px]"> stale</span>}
                  </td>
                  <td className="px-2 py-2">
                    <span className={`inline-flex items-center gap-1 font-bold ${st.t}`}>
                      <span className={`w-1.5 h-1.5 rounded-sm ${st.d}`} />
                      {z.state}
                    </span>
                  </td>
                  <td className={`px-2 py-2 text-right num ${zColor(z.price_z)}`}>
                    {z.price_close != null ? `€${z.price_close.toFixed(0)}` : '—'}
                  </td>
                  <td className={`px-2 py-2 text-right num ${zColor(z.residual_z)}`}>
                    {z.residual_gw != null ? `${z.residual_gw.toFixed(0)} GW` : '—'}
                  </td>
                  <td className="px-3 py-2 text-right num text-neutral-300 whitespace-nowrap">
                    {z.renewable_reliable === false ? '—' : z.renewable_share != null ? `${Math.round(z.renewable_share * 100)}%` : '—'}
                    {z.dunkelflaute && <span className="text-yellow-400" title="Dunkelflaute"> ⚠</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-1 border-t border-border/40 font-mono text-[8px] text-neutral-700 leading-snug">
        Colour = how far each metric sits from its own ~90-day norm (grey normal · amber elevated · red extreme). Descriptive, not a forecast.
      </div>
    </div>
  )
}
