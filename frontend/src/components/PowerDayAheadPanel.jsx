import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Render a red marker on days where negative_hours > 0; invisible otherwise.
// Mirrors the FlagDot pattern from GasBalancePanel.
function NegativeDot({ cx, cy, payload }) {
  if (!payload?.negative || cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={3} fill="#f87171" stroke="none" />
}

export default function PowerDayAheadPanel({ zone = 'DE_LU' }) {
  const url = `${API}/power/day-ahead?days=120&zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone] })

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">POWER DAY-AHEAD // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest ?? rows[rows.length - 1]
  const close = latest?.close
  const negativeDays = data?.negative_days ?? 0
  // Readable label: prefer what the API returns, fall back to the zone prop
  const zoneLabel = data?.zone === 'DE_LU' ? 'DE-LU' : (data?.zone ?? zone)

  return (
    <Panel
      id="power-day-ahead"
      title={`POWER DAY-AHEAD · ${zoneLabel}`}
      info="ENTSO-E day-ahead electricity prices for the selected bidding zone (EUR/MWh). Each point is the daily mean of 24 hourly auction results from the ENTSO-E Transparency Platform (A44). Red markers indicate days with at least one negative-price hour (renewable oversupply). Free, official redistributable data."
      collapsible
      headerRight={
        close != null && (
          <span className="font-mono text-[10px] text-cyan-glow font-bold">
            {close.toFixed(1)} €/MWh
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading power prices…
        </div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-3xl font-bold text-cyan-glow">
                {close?.toFixed(1)}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">EUR/MWh</span>
              <span className="font-mono text-[10px] text-neutral-600">{latest.date}</span>
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-1">
              ENTSO-E A44 · {zoneLabel} bidding zone · daily mean of hourly prices
              {negativeDays > 0 && (
                <span className="ml-2 text-red-400">
                  · {negativeDays} negative-price {negativeDays === 1 ? 'day' : 'days'}
                </span>
              )}
            </div>
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
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
                    width={30}
                  />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v) => [`${Number(v).toFixed(1)} €/MWh`, 'Day-Ahead']}
                    labelFormatter={fmtDate}
                  />
                  <Area
                    type="monotone"
                    dataKey="close"
                    stroke="#22d3ee"
                    fill="#22d3ee"
                    fillOpacity={0.06}
                    strokeWidth={1.5}
                    dot={<NegativeDot />}
                    activeDot={{ r: 3, fill: '#22d3ee' }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
