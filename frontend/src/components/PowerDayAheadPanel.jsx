import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

export default function PowerDayAheadPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/power/day-ahead?days=120`)

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">POWER DAY-AHEAD // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = rows[rows.length - 1]
  const close = latest?.close

  return (
    <Panel
      id="power-day-ahead"
      title="POWER DAY-AHEAD · DE-LU"
      info="ENTSO-E day-ahead electricity prices for the DE-LU bidding zone (EUR/MWh). Each point is the daily mean of 24 hourly auction results from the ENTSO-E Transparency Platform (A44). Free, official redistributable data."
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
              ENTSO-E A44 · DE-LU bidding zone · daily mean of hourly prices
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
                    dot={false}
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
