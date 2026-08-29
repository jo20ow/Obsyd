import { useMemo, useState } from 'react'
import { InfoPopover } from './Panel'
import { StaleChip } from './FreshnessCaption'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'
import { ZONE_STATE, STATE_ORDER, zColor, metricGlossary } from '../utils/zoneState'

// Single-glance overview — read all bidding zones at once, colour-first, like
// Electricity Maps. Colour encodes how far each metric sits from its
// own trailing norm (the window is whatever /overview reports as baseline_days), so the
// European power picture reads in one second. Click a column header to sort;
// what a ROW click does is the caller's (`onSelect`) — on the desk it focuses
// the zone on the map beside it. Descriptive, not a forecast.
const API = '/api'

// State colours + the compact rail's one-letter code now live in
// utils/zoneState.js: the desk rail renders this table and ZoneDetailCard for
// the SAME zone at the same time, so the two must read from one map.
// `compact` = the desk-split rail (~1/3 width, beside the big map): same table,
// same sorting, less horizontal ink. The units move OUT of every cell and INTO
// the header (74 under "€/MWh" instead of €74 under "Day-ahead"), which is both
// shorter per row and where a unit belongs; the State word shrinks to a dot plus
// its one-letter code (see ZONE_STATE.code — the letter is what survives colour
// blindness), with the full word kept for screen readers.
const COLUMNS = [
  { key: 'zone', label: 'Zone', align: 'left', get: (z) => z.zone_label || z.zone },
  // 'ST' rather than '': a sortable column needs a visible thing to click.
  { key: 'state', label: 'State', compactLabel: 'ST', align: 'left', get: (z) => STATE_ORDER[z.state] ?? -1 },
  { key: 'price', label: 'Day-ahead', compactLabel: '€/MWh', align: 'right', get: (z) => z.price_close },
  { key: 'residual', label: 'Residual', compactLabel: 'GW', align: 'right', get: (z) => z.residual_gw },
  { key: 'renewables', label: 'Renewables', compactLabel: 'RES', align: 'right', get: (z) => (z.renewable_reliable === false ? null : z.renewable_share) },
]

// One legend for the whole table (per-column popovers would be clipped by the
// scroll container). The four definitions are SHARED with ZoneDetailCard's ⓘ
// ~200 px below in the same rail (utils/zoneState.js) — they must not be
// rewritten here; this copy hardcoded "30-day" and could contradict the footer
// two lines down, which reads the live baseline_days. Only the map caveat is
// this table's own: Day-ahead here is the DAILY MEAN, the map shades one hour.
const TABLE_INFO = (baselineDays) => {
  const g = metricGlossary(baselineDays)
  return [
    'What each column means.',
    g.state, g.dayAhead,
    'The map shades one hour at a time (its slider), so the map’s number differs from this average.',
    g.residual, g.renewables,
  ].join(' ')
}

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
    // Compact fills the desk rail's column instead of capping itself: the rail
    // is a flex column of a known height, so the card takes what is left
    // (lg:flex-1) and hands it to the scroller below. No max-h — the old
    // `42vh` was a number from nowhere that inner-scrolled at 14 of 37 zones
    // while 400 px of rail sat empty. Only at lg: on a phone the rail has no
    // forced height and the table simply renders, rather than trapping a scroll
    // inside a page scroll.
    <div className={`border border-border bg-surface rounded overflow-hidden shadow-sm ${
      compact ? 'lg:flex-1 lg:min-h-0 lg:flex lg:flex-col' : ''
    }`}>
      <div className="shrink-0 px-4 py-2.5 border-b border-border/60 flex items-center gap-2">
        <span className="font-mono text-[12px] font-semibold text-neutral-300">European power · all zones</span>
        <InfoPopover text={TABLE_INFO(data.baseline_days)} />
        <span className="font-mono text-[9px] text-neutral-700 ml-auto">
          {compact ? 'sort ↕ · click a zone to focus it' : 'sort ↕ · click a zone for detail →'}
        </span>
      </div>
      <div className={`overflow-x-auto overflow-y-auto ${compact ? 'lg:flex-1 lg:min-h-0' : 'max-h-[520px]'}`}>
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
              const st = ZONE_STATE[z.state] || ZONE_STATE.CALM
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
                    {z.stale && <StaleChip asOf={z.as_of} dense className="ml-1" />}
                  </td>
                  {/* Compact swaps the word for the dot + its letter. The letter
                      is the accessibility carrier (colour alone fails red-green
                      CVD); the sr-only word is what assistive tech reads, since
                      a title= on a <td> reaches neither it nor the keyboard. */}
                  <td className={`${cellX} py-2`}>
                    <span className={`inline-flex items-center gap-1 font-bold ${st.text}`}>
                      <span className={`${compact ? 'w-2 h-2' : 'w-1.5 h-1.5'} rounded-sm ${st.dot}`} />
                      {compact ? <span aria-hidden="true">{st.code}</span> : z.state}
                      {compact && <span className="sr-only">{z.state}</span>}
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
      <div className="shrink-0 px-3 py-1 border-t border-border/40 font-mono text-[8px] text-neutral-700 leading-snug">
        Colour = how far each metric sits from its own {data.baseline_days ? `${data.baseline_days}-day` : 'trailing'} norm (grey normal · amber elevated · red extreme). Descriptive, not a forecast.
      </div>
    </div>
  )
}
