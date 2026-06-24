import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'
import TrackRecordBadge from './TrackRecordBadge'

const API = '/api'

// Color palette for the stacked areas
const COLOR_WIND   = '#22d3ee' // cyan
const COLOR_SOLAR  = '#fbbf24' // amber
const COLOR_RESID  = '#94a3b8' // slate (dispatchable)
const COLOR_DUNKEL = '#f87171' // red-400

// Custom dot: renders a small marker on Dunkelflaute days, nothing otherwise.
function DunkelDot({ cx, cy, payload }) {
  if (!payload?.dunkelflaute || cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={3} fill={COLOR_DUNKEL} stroke="none" />
}

export default function PowerGridPanel({ zone = 'DE_LU' }) {
  const url = `${API}/power/grid?days=120&zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone] })

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">RESIDUAL LOAD // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest
  const dunkelflaute_days = data?.dunkelflaute_days ?? 0

  // Residual in GW for the headline
  const residualGW = latest?.residual_mw != null
    ? (latest.residual_mw / 1000).toFixed(1)
    : null
  const sharePercent = latest?.renewable_share != null
    ? (latest.renewable_share * 100).toFixed(1)
    : null

  const isDunkel = latest?.dunkelflaute === true
  const headerColor = isDunkel ? COLOR_DUNKEL : '#22d3ee'

  // Readable label: prefer what the API returns, fall back to zone prop
  const zoneLabel = data?.zone === 'DE_LU' ? 'DE-LU' : (data?.zone ?? zone)

  return (
    <Panel
      id="power-grid"
      title={`RESIDUAL LOAD · ${zoneLabel}`}
      info="Residual load = total electricity demand − wind − solar (MW daily mean). This is the demand that dispatchable plants (gas, coal, nuclear, hydro) must cover. Dunkelflaute = day when renewable share < 15% of total load — a physical stress signal, not a price forecast."
      collapsible
      headerRight={
        residualGW != null && (
          <span className="font-mono text-[10px] font-bold" style={{ color: headerColor }}>
            {residualGW} GW residual
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading grid data…
        </div>
      )}
      {!loading && data?.available && latest && (
        <>
          {/* ── Hero numbers ── */}
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3 flex-wrap">
              <span className="font-mono text-3xl font-bold" style={{ color: headerColor }}>
                {residualGW != null ? `${residualGW} GW` : '—'}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">residual load</span>
              {sharePercent != null && (
                <span className="font-mono text-[10px] text-neutral-300">
                  {sharePercent}% renewables
                </span>
              )}
              {isDunkel && (
                <span
                  className="font-mono text-[9px] tracking-wider px-1.5 py-0.5 rounded border"
                  style={{ color: COLOR_DUNKEL, borderColor: `${COLOR_DUNKEL}40` }}
                >
                  DUNKELFLAUTE
                </span>
              )}
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-1">
              {dunkelflaute_days} Dunkelflaute days / {rows.length} · ENTSO-E A65 + A75 · {zoneLabel} · {latest.date}
            </div>
          </div>

          {/* ── Stacked area chart ── */}
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={120}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    tickFormatter={fmtDate}
                    interval="preserveStartEnd"
                    minTickGap={60}
                  />
                  <YAxis
                    tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }}
                    width={36}
                    tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                  />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v, name) => [`${Math.round(v).toLocaleString()} MW`, name]}
                    labelFormatter={fmtDate}
                  />
                  {/* Stack: wind + solar + residual = total load */}
                  <Area
                    type="monotone"
                    dataKey="wind_mw"
                    name="wind"
                    stackId="load"
                    stroke={COLOR_WIND}
                    fill={COLOR_WIND}
                    fillOpacity={0.18}
                    strokeWidth={1}
                    dot={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="solar_mw"
                    name="solar"
                    stackId="load"
                    stroke={COLOR_SOLAR}
                    fill={COLOR_SOLAR}
                    fillOpacity={0.18}
                    strokeWidth={1}
                    dot={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="residual_mw"
                    name="residual"
                    stackId="load"
                    stroke={COLOR_RESID}
                    fill={COLOR_RESID}
                    fillOpacity={0.12}
                    strokeWidth={1.5}
                    dot={<DunkelDot />}
                    activeDot={{ r: 3 }}
                  />
                </AreaChart>
              </ResponsiveContainer>

              {/* Legend */}
              <div className="flex items-center justify-center gap-4 mt-1 font-mono text-[8px] text-neutral-600">
                <span style={{ color: COLOR_WIND }}>▬ wind</span>
                <span style={{ color: COLOR_SOLAR }}>▬ solar</span>
                <span style={{ color: COLOR_RESID }}>▬ residual (dispatchable)</span>
                <span style={{ color: COLOR_DUNKEL }}>● Dunkelflaute</span>
              </div>
            </div>
          )}
        </>
      )}
      <TrackRecordBadge signal="power_residual" targetLabel="Power" />
    </Panel>
  )
}
