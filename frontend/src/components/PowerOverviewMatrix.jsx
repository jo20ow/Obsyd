import { useMemo, useState } from 'react'
import { InfoPopover } from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'

// Single-glance overview — read all bidding zones at once, colour-first, like
// Electricity Maps. Colour encodes how far each metric sits from its
// own trailing norm (the window is whatever /overview reports as baseline_days), so the
// European power picture reads in one second. Click a column header to sort;
// what a ROW click does is the caller's (`onSelect`) — on the desk it focuses
// the zone on the map beside it. Descriptive, not a forecast.
const API = '/api'

const STATE = {
  CALM: { t: 'text-green-glow', d: 'bg-green-glow' },
  ELEVATED: { t: 'text-yellow-400', d: 'bg-yellow-400' },
  STRESSED: { t: 'text-red-400', d: 'bg-red-400' },
}
const STATE_ORDER = { CALM: 0, ELEVATED: 1, STRESSED: 2 }

const zColor = (z) =>
  z == null ? 'text-neutral-400' : Math.abs(z) >= 3 ? 'text-red-400' : Math.abs(z) >= 2 ? 'text-yellow-400' : 'text-neutral-300'

// `compact` = the desk-split rail (~1/3 width, beside the big map): same table,
// same sorting, less horizontal ink. The units move OUT of every cell and INTO
// the header (74 under "€/MWh" instead of €74 under "Day-ahead"), which is both
// shorter per row and where a unit belongs; the State word becomes its dot
// alone (the word stays reachable as the cell's title=).
const COLUMNS = [
  { key: 'zone', label: 'Zone', align: 'left', get: (z) => z.zone_label || z.zone },
  { key: 'state', label: 'State', compactLabel: '', align: 'left', get: (z) => STATE_ORDER[z.state] ?? -1 },
  { key: 'price', label: 'Day-ahead', compactLabel: '€/MWh', align: 'right', get: (z) => z.price_close },
  { key: 'residual', label: 'Residual', compactLabel: 'GW', align: 'right', get: (z) => z.residual_gw },
  { key: 'renewables', label: 'Renewables', compactLabel: 'RES', align: 'right', get: (z) => (z.renewable_reliable === false ? null : z.renewable_share) },
]

// One legend for the whole table (per-column popovers would be clipped by the
// scroll container). Spells out what each column is — and, crucially, that
// Day-ahead here is the DAILY MEAN, while the map shades a single hour.
const TABLE_INFO = (
  'What each column means. '
  + 'State: how far this zone sits from its own 30-day norm — CALM / ELEVATED (amber) / STRESSED (red); a deviation vs history, not a forecast. '
  + 'Day-ahead: the auction price (€/MWh), cleared the day before for this delivery day — a settled market price, NOT a forecast. It is the DAILY MEAN across the day’s hours; the map shades one hour at a time (its slider), so the map’s number differs from this average. '
  + 'Residual: demand − wind − solar (GW), the gap conventional plants must fill — what actually sets the price. '
  + 'Renewables: wind + solar as a share of load, left blank when the feed is too incomplete to trust the share.'
)

export default function PowerOverviewMatrix({ selectedZone, onSelect, compact = false }) {
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
  // One padding pair for the whole table: `edge` is the two outer columns.
  const cellX = compact ? 'px-1.5' : 'px-2'
  const edgeX = compact ? 'px-1.5' : 'px-3'

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="px-4 py-2.5 border-b border-border/60 flex items-center gap-2">
        <span className="font-mono text-[12px] font-semibold text-neutral-300">European power · all zones</span>
        <InfoPopover text={TABLE_INFO} />
        <span className="font-mono text-[9px] text-neutral-700 ml-auto">
          {compact ? 'sort ↕ · click a zone to focus it' : 'sort ↕ · click a zone for detail →'}
        </span>
      </div>
      <div className={`overflow-x-auto ${compact ? 'max-h-[42vh]' : 'max-h-[520px]'} overflow-y-auto`}>
        <table className="w-full font-mono text-[11px]">
          <thead className="sticky top-0 bg-surface">
            <tr className="text-[9px] text-neutral-500">
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => toggle(c.key)}
                  title={compact ? c.label : undefined}
                  className={`${c.align === 'right' ? 'text-right' : 'text-left'} ${c.key === 'zone' || c.key === 'renewables' ? edgeX : cellX} py-1 font-normal cursor-pointer hover:text-neutral-300 select-none`}
                >
                  {compact && c.compactLabel != null ? c.compactLabel : c.label}{arrow(c.key)}
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
                  // The selected row's left bar is what ties it to the outlined
                  // zone on the map. Unselected rows carry the same 2 px as
                  // transparent, so selecting never nudges the row sideways.
                  className={`cursor-pointer border-t border-border/40 border-l-2 hover:bg-white/[0.03] ${sel ? 'bg-cyan-glow/5 border-l-cyan-glow' : 'border-l-transparent'}`}
                >
                  <td className={`${edgeX} py-2 text-neutral-200 whitespace-nowrap`}>
                    {z.zone_label}
                    {sel && <span className="text-cyan-glow"> ‹</span>}
                    {z.stale && <span className="text-orange-400/70 text-[8px]"> stale</span>}
                  </td>
                  {/* Compact drops the word and keeps the dot — title= keeps it
                      readable, and the row's colour is not the only carrier
                      (the zone's own state column stays sortable). */}
                  <td className={`${cellX} py-2`} title={z.state}>
                    <span className={`inline-flex items-center gap-1 font-bold ${st.t}`}>
                      <span className={`${compact ? 'w-2 h-2' : 'w-1.5 h-1.5'} rounded-sm ${st.d}`} />
                      {!compact && z.state}
                    </span>
                  </td>
                  <td className={`${cellX} py-2 text-right num ${zColor(z.price_z)}`}>
                    {z.price_close != null ? `${compact ? '' : '€'}${z.price_close.toFixed(0)}` : '—'}
                  </td>
                  <td className={`${cellX} py-2 text-right num ${zColor(z.residual_z)}`}>
                    {z.residual_gw != null ? `${z.residual_gw.toFixed(0)}${compact ? '' : ' GW'}` : '—'}
                  </td>
                  <td className={`${edgeX} py-2 text-right num text-neutral-300 whitespace-nowrap`}>
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
        Colour = how far each metric sits from its own {data.baseline_days ? `${data.baseline_days}-day` : 'trailing'} norm (grey normal · amber elevated · red extreme). Descriptive, not a forecast.
      </div>
    </div>
  )
}
