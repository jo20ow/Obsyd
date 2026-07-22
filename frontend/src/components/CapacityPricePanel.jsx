import { useState } from 'react'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts'
import { fmtTs, CHART_TOOLTIP_PROPS } from '../utils/chart'

const API = '/api'

// Deliberately NOT the fuel palette (frontend/src/utils/fuels.js) — this is a price chart,
// not a generation mix. FCR is neutral/gray (symmetric, no direction). aFRR reuses
// BalancingPanel's own up/down convention (cyan/amber) so the two capacity+activation panels
// share one mental model; mFRR gets its own cool/warm pair so all 5 lines stay distinguishable.
const PRODUCTS = [
  { key: 'fcr', label: 'FCR', color: '#e5e5e5' },
  { key: 'afrr_pos', label: 'aFRR ↑', color: '#22d3ee' },
  { key: 'afrr_neg', label: 'aFRR ↓', color: '#fbbf24' },
  { key: 'mfrr_pos', label: 'mFRR ↑', color: '#e879f9' },
  { key: 'mfrr_neg', label: 'mFRR ↓', color: '#a3e635' },
]

const WINDOW_DAYS = 30

function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

function fmtPrice(v) {
  if (v == null) return '—'
  return `€${v.toFixed(2)}`
}

// Static explanation of the panel — always shown, since the backend's own `note` (present
// whenever available:true) is a shorter one-liner and would otherwise replace this instead of
// supplementing it (BalancingPanel's INFO_TEXT pattern).
const INFO_TEXT =
  'German balancing-capacity market (ENTSO-E A15) — DE-LU LFC block only, structurally: ' +
  'individual German control areas publish nothing at this domain, and no other enabled zone ' +
  'has an equivalent tender. Each line is the volume-weighted average of that block\'s ' +
  "accepted capacity bids, normalized to EUR/MW/h (FCR's native EUR-per-4h-block price is " +
  'divided by 4). FCR is pay-as-cleared and symmetric; aFRR/mFRR are pay-as-bid and split by ' +
  'direction. FCR settlement can differ slightly from this DE-LU-block reconstruction on days ' +
  'when cross-border export limits bind. All times UTC. Descriptive, not a forecast.'

/**
 * German procured balancing-CAPACITY prices (ENTSO-E A15: FCR/aFRR/mFRR daily tenders) — what
 * Germany pays to have reserve capacity on standby, before any of it is ever activated (the
 * companion number to BalancingPanel's activation prices). DE-LU LFC block only, structurally
 * — not a coverage gap, so every other zone renders a calm explanation instead of vanishing.
 * Descriptive, not a forecast (Posture B).
 */
export default function CapacityPricePanel({ zone = 'DE_LU' }) {
  const [hidden, setHidden] = useState(() => new Set())
  const url = `${API}/power/capacity-prices?zone=${zone}&days=${WINDOW_DAYS}`
  const { data, loading, error } = useFetchWithError(url, {
    deps: [zone],
    pollMs: POLL_SLOW_MS,
  })

  // A transient poll failure must not blank a chart that already has good (if slightly
  // stale) data — mirrors BalancingPanel/LiveNowPanel.
  if (error && !data)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">CAPACITY PRICES // FETCH ERROR</div>
      </div>
    )

  const zl = data?.zone_label ?? zoneLabel(zone)
  const latest = data?.latest ?? {}

  // Merge the 5 product series on timestamp (hour-aligned epoch ms, not the raw string) —
  // each series is already densified per-hour by the backend (a block's weighted-average
  // price holds for every hour it covers), so no client-side gap-filling is needed here,
  // unlike BalancingPanel's genuinely-intermittent activation data.
  const rowsByT = new Map()
  for (const p of PRODUCTS) {
    for (const point of data?.products?.[p.key] ?? []) {
      const k = Date.parse(point.t)
      const row = rowsByT.get(k) ?? { t: point.t }
      row[p.key] = point.price
      rowsByT.set(k, row)
    }
  }
  const rows = [...rowsByT.entries()].sort(([a], [b]) => a - b).map(([, row]) => row)

  function toggle(key) {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <Panel
      id="power-capacity-prices"
      freshness={data}
      title={`CAPACITY PRICES · ${zl}`}
      info={data?.note ? `${data.note} ${INFO_TEXT}` : INFO_TEXT}
      collapsible
      downloadUrl={
        data?.available
          ? `${API}/v1/series?series=capacity.fcr.price&zone=DE_LU&format=csv`
          : undefined
      }
      headerRight={
        data?.available &&
        latest.fcr?.price != null && (
          <span className="font-mono text-[10px] font-bold text-neutral-300">
            FCR {fmtPrice(latest.fcr.price)}/MW/h
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading capacity prices…
        </div>
      )}

      {/* available:false is the common, structural case for every zone except DE_LU — render
          it calmly, not as an error, and never let the panel vanish. */}
      {!loading && data && !data.available && (
        <div className="px-4 py-3 font-mono text-[10px] text-neutral-500">
          {data.reason || `No German balancing-capacity data for ${zl} yet.`}
        </div>
      )}

      {!loading && data?.available && (
        <>
          <div className="px-4 pt-2">
            <ResponsiveContainer width="100%" height={170}>
              <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
                <XAxis
                  dataKey="t"
                  tickFormatter={fmtTs}
                  tick={{ fontSize: 9, fill: '#525252' }}
                  minTickGap={60}
                />
                <YAxis
                  tick={{ fontSize: 9, fill: '#525252' }}
                  width={44}
                  tickFormatter={(v) => `${v}`}
                />
                <Tooltip
                  {...CHART_TOOLTIP_PROPS}
                  formatter={(v, name) => [v == null ? '—' : `€${Number(v).toFixed(2)}/MW/h`, name]}
                  labelFormatter={fmtTs}
                />
                {/* stepAfter: each point is a 4-hour tender block's price holding constant —
                    a true step function, not an interpolated trend. No dots (unlike
                    BalancingPanel) — capacity blocks are densely populated every day, so a
                    lone-point marker isn't needed to keep a sparse activation visible. */}
                {PRODUCTS.map((p) => (
                  <Line
                    key={p.key}
                    type="stepAfter"
                    dataKey={p.key}
                    name={p.label}
                    stroke={p.color}
                    strokeWidth={1.25}
                    dot={false}
                    hide={hidden.has(p.key)}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
            <div className="flex items-center justify-center flex-wrap gap-3 mt-1 font-mono text-[8px]">
              {PRODUCTS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => toggle(p.key)}
                  className="transition-opacity"
                  style={{ color: p.color, opacity: hidden.has(p.key) ? 0.35 : 1 }}
                  title={hidden.has(p.key) ? `Show ${p.label}` : `Hide ${p.label}`}
                >
                  ▬ {p.label}
                </button>
              ))}
            </div>
          </div>
          <div className="px-4 py-2">
            <PanelTakeaway>
              Last {data?.days ?? WINDOW_DAYS}d · FCR {fmtPrice(latest.fcr?.price)} · aFRR{' '}
              {fmtPrice(latest.afrr_pos?.price)}/{fmtPrice(latest.afrr_neg?.price)} · mFRR{' '}
              {fmtPrice(latest.mfrr_pos?.price)}/{fmtPrice(latest.mfrr_neg?.price)} per MW/h.
            </PanelTakeaway>
          </div>
        </>
      )}
    </Panel>
  )
}
