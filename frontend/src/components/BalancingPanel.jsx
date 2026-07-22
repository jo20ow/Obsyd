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
  ReferenceLine,
} from 'recharts'
import { fmtTs, CHART_TOOLTIP_PROPS } from '../utils/chart'

const API = '/api'

// Colors mirror the house up/secondary convention (PowerGridPanel's
// wind/solar pair, PowerLoadForecastPanel's actual/forecast pair): the
// established default price-line cyan for the first series, amber for the
// second. Direction is never color-alone — arrows + a legend carry identity too.
const COLOR_UP = '#22d3ee'
const COLOR_DOWN = '#fbbf24'

function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

function ProductToggle({ product, onChange }) {
  return (
    <div className="flex gap-1">
      {['afrr', 'mfrr'].map((p) => (
        <button
          key={p}
          onClick={() => onChange(p)}
          className={`font-mono text-[9px] tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
            product === p
              ? 'border-cyan-glow/40 text-cyan-glow'
              : 'border-border text-neutral-600 hover:text-neutral-400'
          }`}
        >
          {p === 'afrr' ? 'aFRR' : 'mFRR'}
        </button>
      ))}
    </div>
  )
}

// "↑ €82.4" vs "↓ −€12.0" — direction as a glyph, never color alone.
function fmtDirPrice(price, direction) {
  if (price == null) return '—'
  const arrow = direction === 'down' ? '↓' : direction === 'up' ? '↑' : ''
  const sign = price < 0 ? '−' : ''
  return `${arrow} ${sign}€${Math.abs(price).toFixed(1)}`.trim()
}

function dirColor(direction) {
  return direction === 'down' ? COLOR_DOWN : direction === 'up' ? COLOR_UP : '#a3a3a3'
}

const HOUR_MS = 60 * 60 * 1000
const WINDOW_DAYS = 30

// Static explanation of the panel — always shown, since the backend's own
// `note` (present whenever available:true) is a shorter one-liner and would
// otherwise replace this instead of supplementing it (like ImbalancePanel's
// info popover, which is likewise always the full explanation).
const INFO_TEXT =
  'Activated balancing energy (ENTSO-E A84) — what the TSO actually called on beyond ' +
  'the day-ahead auction, by direction. Coverage varies by zone/product — many combinations ' +
  "publish nothing on a given day. Activation volumes aren't currently served by the public " +
  'API for any zone. Gaps in the chart mean no activation was published for that direction/' +
  'hour, not missing data — the line never holds a price forward across them. All times UTC. ' +
  'Descriptive, not a forecast.'

/**
 * Activated balancing energy (ENTSO-E A84 aFRR/mFRR prices) — what the TSO
 * actually called on beyond the day-ahead auction to keep the grid balanced,
 * split by direction. Coverage is genuinely patchy: many zone/product
 * combinations publish nothing on a given day, and that is normal, not an
 * error (see backend/power/entsoe_balancing.py's live-spike docstring).
 * Activation VOLUMES (A83) are not served by the public API at all today —
 * this panel is prices only. Descriptive, not a forecast (Posture B).
 */
export default function BalancingPanel({ zone = 'DE_LU' }) {
  const [product, setProduct] = useState('afrr')
  const url = `${API}/power/balancing?zone=${zone}&days=${WINDOW_DAYS}&product=${product}`
  const { data, loading, error } = useFetchWithError(url, {
    deps: [zone, product],
    pollMs: POLL_SLOW_MS,
  })

  // A transient poll failure must not blank a chart that already has good (if
  // slightly stale) data — mirrors LiveNowPanel/PowerSituationHeader.
  if (error && !data)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">BALANCING // FETCH ERROR</div>
      </div>
    )

  const zl = data?.zone_label ?? zoneLabel(zone)
  const productLabel = product === 'afrr' ? 'aFRR' : 'mFRR'
  const latest = data?.latest
  const peak = data?.peak
  const unit = data?.unit ?? 'EUR/MWh'

  // Merge the up/down direction series on timestamp, keyed by hour-aligned
  // epoch ms (not the raw string) so a real point always coalesces with its
  // filler below instead of duplicating under a differently-formatted key.
  const rowsByT = new Map()
  for (const p of data?.up ?? []) rowsByT.set(Date.parse(p.t), { t: p.t, up: p.price })
  for (const p of data?.down ?? []) {
    const k = Date.parse(p.t)
    const row = rowsByT.get(k) ?? { t: p.t }
    row.down = p.price
    rowsByT.set(k, row)
  }
  // Densify over the full hourly grid, window-start (now − 30d, aligned to the
  // hour) to the latest published hour: an hour where NEITHER direction
  // activated must still get its own category, or two activations days apart
  // would sit in adjacent chart categories joined by a line — the exact
  // fabricated continuity dropping connectNulls (below) is meant to prevent.
  if (rowsByT.size) {
    const latestMs = Math.max(...rowsByT.keys())
    const startMs = Math.floor((new Date().getTime() - WINDOW_DAYS * 24 * HOUR_MS) / HOUR_MS) * HOUR_MS
    for (let ms = startMs; ms <= latestMs; ms += HOUR_MS) {
      if (!rowsByT.has(ms)) rowsByT.set(ms, { t: new Date(ms).toISOString() })
    }
  }
  const rows = [...rowsByT.entries()].sort(([a], [b]) => a - b).map(([, row]) => row)

  return (
    <Panel
      id="power-balancing"
      freshness={data}
      title={`BALANCING · ${zl}`}
      info={data?.note ? `${data.note} ${INFO_TEXT}` : INFO_TEXT}
      collapsible
      downloadUrl={
        data?.available
          ? `${API}/v1/series?series=balancing.${product}.price.up&zone=${zone}&format=csv`
          : undefined
      }
      headerRight={
        <div className="flex items-center gap-2">
          <ProductToggle product={product} onChange={setProduct} />
          {data?.available && latest?.price != null && (
            <span className="font-mono text-[10px] font-bold" style={{ color: dirColor(latest.direction) }}>
              {fmtDirPrice(latest.price, latest.direction)}
            </span>
          )}
        </div>
      }
    >
      {data?.coverage && (
        <div className="px-4 pt-2 font-mono text-[9px] text-amber-400/80">
          ⚠ {data.coverage}
        </div>
      )}

      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading balancing prices…
        </div>
      )}

      {/* available:false is the COMMON case (many zone/product combinations publish
          nothing) — render it calmly, not as an error, and keep the product toggle
          live so a reader can flip straight to the product that does have data. */}
      {!loading && data && !data.available && (
        <div className="px-4 py-3 font-mono text-[10px] text-neutral-500">
          {data.reason || `No ${productLabel} activated-balancing-energy series for ${zl} yet.`}
        </div>
      )}

      {!loading && data?.available && (
        <>
          <div className="px-4 pt-2">
            <ResponsiveContainer width="100%" height={160}>
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
                <ReferenceLine y={0} stroke="#2a2a3a" strokeDasharray="2 2" />
                <Tooltip
                  {...CHART_TOOLTIP_PROPS}
                  formatter={(v, name) => [v == null ? '—' : `${Number(v).toFixed(1)} ${unit}`, name]}
                  labelFormatter={fmtTs}
                />
                {/* No connectNulls: the backend deliberately never holds a price
                    forward across an hour nothing activated in (episodic mFRR
                    especially — 25 vs 444 TimeSeries in the spiked TenneT month).
                    The hourly grid above densifies `rows` so a genuinely silent
                    hour always gets its own null category instead of vanishing —
                    without that, two activations days apart would sit in
                    adjacent categories joined by a line, the very thing dropping
                    connectNulls is meant to prevent. A dot (explicitly filled —
                    Recharts' own default dot fill is white, not the line color)
                    keeps a lone activation hour visible without a line to carry it. */}
                <Line
                  type="stepAfter"
                  dataKey="up"
                  name="up-regulation"
                  stroke={COLOR_UP}
                  strokeWidth={1}
                  dot={{ r: 1.5, strokeWidth: 0, fill: COLOR_UP }}
                  isAnimationActive={false}
                />
                <Line
                  type="stepAfter"
                  dataKey="down"
                  name="down-regulation"
                  stroke={COLOR_DOWN}
                  strokeWidth={1}
                  dot={{ r: 1.5, strokeWidth: 0, fill: COLOR_DOWN }}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
            <div className="flex items-center justify-center gap-4 mt-1 font-mono text-[8px] text-neutral-600">
              <span style={{ color: COLOR_UP }}>▬ up-regulation (↑)</span>
              <span style={{ color: COLOR_DOWN }}>▬ down-regulation (↓)</span>
            </div>
          </div>
          <div className="px-4 py-2">
            <PanelTakeaway>
              Last {data?.days ?? WINDOW_DAYS}d · latest {productLabel} activation:{' '}
              {fmtDirPrice(latest?.price, latest?.direction)}/MWh
              {peak && ` · window peak ${fmtDirPrice(peak.price, peak.direction)}/MWh (${fmtTs(peak.t)})`}.{' '}
              Volumes aren't published by the public API today — this is prices only.
            </PanelTakeaway>
          </div>
        </>
      )}
    </Panel>
  )
}
