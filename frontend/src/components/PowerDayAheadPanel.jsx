import { useState } from 'react'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeDays, rangeStart } from '../utils/ranges'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, fmtHour, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Render a red marker on days where negative_hours > 0; invisible otherwise.
// Mirrors the FlagDot pattern from GasBalancePanel.
function NegativeDot({ cx, cy, payload }) {
  if (!payload?.negative || cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={3} fill="#f87171" stroke="none" />
}

export default function PowerDayAheadPanel({ zone = 'DE_LU' }) {
  const { range } = useViewState()
  const url = `${API}/power/day-ahead?days=${rangeDays(range)}&zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone, range] })
  // The 24h shape behind the daily mean (peak/off-peak). Latest day only, so it is
  // independent of the global range. The toggle just swaps which series is charted.
  const { data: hourlyData } = useFetchWithError(`${API}/power/day-ahead/hourly?zone=${zone}`, { deps: [zone] })
  const [view, setView] = useState('daily')

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
  // "vs normal": where does today's price sit in its own recent range? (the single
  // biggest legibility win — a bare €/MWh teaches nothing without this anchor.)
  const closes = rows.map((r) => r.close).filter((v) => v != null)
  const pctile = closes.length && close != null
    ? Math.round((closes.filter((v) => v < close).length / closes.length) * 100)
    : null
  // Readable label: prefer what the API returns, fall back to the zone prop
  const zoneLabel = data?.zone === 'DE_LU' ? 'DE-LU' : (data?.zone ?? zone)
  const hourlyAvail = !!hourlyData?.available && Array.isArray(hourlyData?.data) && hourlyData.data.length > 0
  const hourly = hourlyAvail ? hourlyData.data : []

  return (
    <Panel
      id="power-day-ahead"
      freshness={data}
      title={`POWER DAY-AHEAD · ${zoneLabel}`}
      info="ENTSO-E day-ahead electricity prices for the selected bidding zone (EUR/MWh). Daily view = the mean of 24 hourly auction results (A44); red markers flag days with a negative-price hour (renewable oversupply). Toggle to HOURLY for the 24h peak/off-peak shape of the latest day. Free, official redistributable data."
      collapsible
      downloadUrl={`${API}/v1/series?series=price.dayahead&zone=${zone}&start=${rangeStart(range)}&resolution=daily&format=csv`}
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
              <span className="font-mono text-2xl font-bold text-cyan-glow">
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
            <PanelTakeaway className="mt-2">
              {`Wholesale power is €${close?.toFixed(0)}/MWh${
                pctile != null ? ` — higher than ${pctile}% of the last ${closes.length} days` : ' (next-day delivery price)'
              }.`}
              {negativeDays > 0
                ? ` Prices went negative on ${negativeDays} of those days — renewables briefly out-supplied demand.`
                : ''}
            </PanelTakeaway>
          </div>
          {/* Daily-mean ⇄ hourly-shape toggle (hourly = the 24h peak/off-peak curve). */}
          {hourlyAvail && (
            <div className="flex items-center gap-1 px-4 pt-2">
              {['daily', 'hourly'].map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
                    view === v
                      ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10'
                      : 'text-neutral-500 border-border hover:text-neutral-300'
                  }`}
                >
                  {v === 'daily' ? 'DAILY MEAN' : 'HOURLY'}
                </button>
              ))}
              {view === 'hourly' && hourlyData?.date && (
                <span className="font-mono text-[9px] text-neutral-600 ml-1">{hourlyData.date} · UTC</span>
              )}
            </div>
          )}

          {view === 'hourly' && hourlyAvail ? (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={120}>
                <AreaChart data={hourly} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="hour"
                    tickFormatter={fmtHour}
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    interval={2}
                  />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={30} />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v) => [`${Number(v).toFixed(1)} €/MWh`, 'Hourly']}
                    labelFormatter={(h) => `${fmtHour(h)} UTC`}
                  />
                  <Area
                    type="monotone"
                    dataKey="price"
                    stroke="#22d3ee"
                    fill="#22d3ee"
                    fillOpacity={0.06}
                    strokeWidth={1.5}
                    dot={false}
                    activeDot={{ r: 3, fill: '#22d3ee' }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            rows.length > 1 && (
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
            )
          )}
        </>
      )}
    </Panel>
  )
}
