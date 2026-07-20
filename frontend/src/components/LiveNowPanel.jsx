import { useState } from 'react'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'
import {
  ResponsiveContainer, ComposedChart, Area, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { fmtHour, fmtTs, CHART_TOOLTIP_PROPS } from '../utils/chart'
import { fuelColor, fuelLabel, sortFuels } from '../utils/fuels'

const API = '/api'

function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

// Negative day-ahead hours get a red marker, same idea as PowerDayAheadPanel's
// NegativeDot but keyed off the point's own price sign (there is no daily flag here).
function NegativeHourDot({ cx, cy, payload }) {
  if (payload?.price == null || payload.price >= 0 || cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={3} fill="#f87171" stroke="none" />
}

function StatTile({ label, value }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[9px] text-neutral-600 tracking-wider uppercase">{label}</div>
      <div className="font-mono text-sm font-bold text-neutral-200 truncate">{value}</div>
    </div>
  )
}

/**
 * The desk's missing "today" read — the daily rollup panels only ever show
 * COMPLETE days, so nothing on the desk showed today until the nightly job
 * closed it out. This reads backend/power/live.py::compute_live directly:
 * today's published actuals (load, per-fuel generation) hour by hour, gaps
 * where ENTSO-E hasn't published yet, next to the day-ahead forecast/price for
 * the SAME hours. Descriptive (Posture B) — never a prediction of its own.
 */
export default function LiveNowPanel({ zone = 'DE_LU' }) {
  const [view, setView] = useState('generation')
  const url = `${API}/power/live?zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone], pollMs: POLL_FAST_MS })

  // A transient poll failure must not blank a chart that already has good (if
  // slightly stale) data — mirrors PowerSituationHeader/OutagePanel/GenMixHistoryPanel.
  if (error && !data)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">LIVE NOW // FETCH ERROR</div>
      </div>
    )
  // Never vanish silently: say why there is no live read instead of rendering nothing.
  if (!data?.available && !loading)
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          LIVE NOW · {zoneLabel(zone)} — {data?.reason || 'no live data for this zone yet.'}
        </div>
      </div>
    )

  const hours = data?.hours ?? []
  const zl = data?.zone_label ?? zoneLabel(zone)
  const showingToday = data?.showing === 'today'
  const summary = data?.summary ?? {}

  // Every fuel that published at least one point today, canonically colored/ordered
  // (the raw ENTSO-E gen.<Bxx> codes — fuelColor/sortFuels/fuelLabel all resolve them).
  const seenFuels = new Set()
  for (const h of hours) for (const k of Object.keys(h.gen || {})) seenFuels.add(k)
  const fuels = sortFuels([...seenFuels])

  const genRows = hours.map((h, i) => ({ hour: i, load: h.load, load_fc: h.load_fc, ...h.gen }))
  const priceRows = hours.map((h, i) => ({ hour: i, price: h.price }))
  // "now" marker on the price chart: the current UTC hour is meaningless once we've
  // fallen back to showing yesterday (there is no "now" on that axis).
  const nowHour = showingToday ? new Date().getUTCHours() : null

  const loadPct = summary.load_vs_forecast_pct
  const genGw = summary.gen_total_now != null ? (summary.gen_total_now / 1000).toFixed(1) : null
  const priceNow = summary.price_now

  return (
    <Panel
      id="power-live"
      freshness={data}
      title={`LIVE NOW · ${zl}`}
      info={data?.note || "Today's published actuals vs the day-ahead forecast/price, hour by hour."}
      collapsible
      headerRight={
        data?.available && (
          showingToday ? (
            <span className="inline-flex items-center gap-1 font-mono text-[9px] text-green-glow border border-green-500/30 rounded px-1.5 py-0.5">
              <span className="w-1.5 h-1.5 rounded-full bg-green-glow animate-pulse" />
              LIVE · lag {data.lag_minutes}m
            </span>
          ) : (
            <span
              className="font-mono text-[9px] text-orange-400 border border-orange-500/30 rounded px-1.5 py-0.5"
              title="Today has no published actuals yet — showing yesterday's complete day instead."
            >
              YESTERDAY
            </span>
          )
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading live power…
        </div>
      )}
      {!loading && data?.available && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="grid grid-cols-3 gap-3">
              <StatTile
                label="Load vs forecast"
                value={loadPct != null ? `${loadPct >= 0 ? '+' : ''}${loadPct.toFixed(1)}%` : '—'}
              />
              <StatTile
                label={showingToday ? 'Generation now' : 'Generation, latest hour'}
                value={genGw != null ? `${genGw} GW` : '—'}
              />
              <StatTile
                label={showingToday ? 'Day-ahead price now' : 'Day-ahead price, latest hour'}
                value={priceNow != null ? `€${priceNow.toFixed(1)}/MWh` : 'n/a'}
              />
            </div>
            <PanelTakeaway className="mt-2">
              {showingToday
                ? `Actuals published through ${fmtTs(data.latest_actual_ts)} (${data.lag_minutes}m lag).`
                : "Today's first actual hasn't published yet — this is yesterday's complete day."}
            </PanelTakeaway>
          </div>

          <div className="flex items-center gap-1 px-4 pt-2">
            {['generation', 'price'].map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
                  view === v
                    ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10'
                    : 'text-neutral-500 border-border hover:text-neutral-300'
                }`}
              >
                {v === 'generation' ? 'GENERATION' : 'PRICE'}
              </button>
            ))}
          </div>

          {view === 'generation' ? (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={200}>
                <ComposedChart data={genRows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="hour"
                    tickFormatter={fmtHour}
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    interval={2}
                  />
                  <YAxis
                    tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }}
                    width={36}
                    tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                  />
                  <Tooltip
                    {...CHART_TOOLTIP_PROPS}
                    formatter={(v, name) => [v == null ? '—' : `${Math.round(v).toLocaleString()} MW`, name]}
                    labelFormatter={(h) => `${fmtHour(h)} UTC`}
                  />
                  {fuels.map((fuel) => (
                    <Area
                      key={fuel}
                      type="monotone"
                      dataKey={fuel}
                      name={fuelLabel(fuel)}
                      stackId="gen"
                      stroke={fuelColor(fuel)}
                      fill={fuelColor(fuel)}
                      fillOpacity={0.18}
                      strokeWidth={1}
                      dot={false}
                      isAnimationActive={false}
                    />
                  ))}
                  <Line
                    type="monotone"
                    dataKey="load"
                    name="load"
                    stroke="#e5e7eb"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="load_fc"
                    name="load forecast"
                    stroke="#e5e7eb"
                    strokeDasharray="4 3"
                    strokeWidth={1}
                    dot={false}
                    connectNulls
                    isAnimationActive={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
              {fuels.length > 0 && (
                <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 mt-1 font-mono text-[8px] text-neutral-600">
                  {fuels.map((fuel) => (
                    <span key={fuel} style={{ color: fuelColor(fuel) }}>▬ {fuelLabel(fuel)}</span>
                  ))}
                  <span>— load</span>
                  <span>┄ forecast</span>
                </div>
              )}
            </div>
          ) : (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={160}>
                <ComposedChart data={priceRows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="hour"
                    tickFormatter={fmtHour}
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    interval={2}
                  />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={36} />
                  <ReferenceLine y={0} stroke="#2a2a3a" strokeDasharray="2 2" />
                  {nowHour != null && (
                    <ReferenceLine
                      x={nowHour}
                      stroke="#22d3ee"
                      strokeDasharray="3 3"
                      label={{ value: 'now', position: 'insideTop', fill: '#22d3ee', fontSize: 9 }}
                    />
                  )}
                  <Tooltip
                    {...CHART_TOOLTIP_PROPS}
                    formatter={(v) => [v == null ? '—' : `${Number(v).toFixed(1)} €/MWh`, 'day-ahead']}
                    labelFormatter={(h) => `${fmtHour(h)} UTC`}
                  />
                  <Area
                    type="stepAfter"
                    dataKey="price"
                    stroke="#22d3ee"
                    fill="#22d3ee"
                    fillOpacity={0.08}
                    strokeWidth={1.5}
                    dot={<NegativeHourDot />}
                    isAnimationActive={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
