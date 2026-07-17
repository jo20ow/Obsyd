import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import { useViewState } from '../context/ViewStateContext'
import { rangeDays, rangeStart } from '../utils/ranges'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_PROPS } from '../utils/chart'
import { fuelColor, sortFuels } from '../utils/fuels'

const API = '/api'

export default function GenerationMixPanel({ zone = 'DE_LU' }) {
  const { range } = useViewState()
  const url = `${API}/power/generation-mix?days=${rangeDays(range)}&zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone, range], pollMs: POLL_SLOW_MS })

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">GENERATION MIX // FETCH ERROR</div>
      </div>
    )
  // Never vanish silently: say why there is no chart instead of rendering nothing.
  if (!data?.available && !loading)
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          GENERATION MIX · {zone === 'DE_LU' ? 'DE-LU' : zone} — {data?.reason || 'no generation data for this zone yet.'}
        </div>
      </div>
    )

  const rows = data?.data ?? []
  const latest = data?.latest
  const types = sortFuels(data?.types ?? [])

  // Top type by generation in latest snapshot
  const topType = latest
    ? Object.entries(latest)
        .filter(([k]) => k !== 'date' && k !== 'total_mw')
        .sort(([, a], [, b]) => b - a)[0]?.[0]
    : null

  const totalGW = latest?.total_mw != null
    ? (latest.total_mw / 1000).toFixed(1)
    : null

  // Readable label: prefer what the API returns, fall back to zone prop
  const zoneLabel = data?.zone === 'DE_LU' ? 'DE-LU' : (data?.zone ?? zone)

  return (
    <Panel
      id="generation-mix"
      freshness={data}
      title={`GENERATION MIX · ${zoneLabel}`}
      info="Full ENTSO-E A75 generation breakdown by production type (daily mean MW). Covers nuclear, coal, gas, hydro, biomass, wind, solar and more. Source: ENTSO-E Transparency Platform, processType A16."
      collapsible
      defaultCollapsed
      downloadUrl={`${API}/v1/genmix?zone=${zone}&start=${rangeStart(range)}&resolution=daily&format=csv`}
      headerRight={
        totalGW != null && (
          <span className="font-mono text-[10px] font-bold text-cyan-400">
            {totalGW} GW total
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading generation mix…
        </div>
      )}
      {!loading && data?.available && latest && (
        <>
          {/* ── Hero numbers ── */}
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3 flex-wrap">
              <span className="font-mono text-2xl font-bold text-cyan-400">
                {totalGW != null ? `${totalGW} GW` : '—'}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">total generation</span>
              {topType && (
                <span
                  className="font-mono text-[10px] font-semibold"
                  style={{ color: fuelColor(topType) }}
                >
                  top: {topType} ({((latest[topType] / latest.total_mw) * 100).toFixed(1)}%)
                </span>
              )}
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-1">
              {types.length} types · ENTSO-E A75 · {zoneLabel} · {latest.date}
            </div>
          </div>

          {/* ── Stacked area chart ── */}
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={160}>
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
                    {...CHART_TOOLTIP_PROPS}
                    formatter={(v, name) => [`${Math.round(v).toLocaleString()} MW`, name]}
                    labelFormatter={fmtDate}
                  />
                  {types.map((type) => (
                    <Area
                      key={type}
                      type="monotone"
                      dataKey={type}
                      name={type}
                      stackId="mix"
                      stroke={fuelColor(type)}
                      fill={fuelColor(type)}
                      fillOpacity={0.18}
                      strokeWidth={1}
                      dot={false}
                      connectNulls
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>

              {/* Legend — two rows max, 8 per row */}
              <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 mt-1 font-mono text-[8px] text-neutral-600">
                {types.map((type) => (
                  <span key={type} style={{ color: fuelColor(type) }}>
                    ▬ {type}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
