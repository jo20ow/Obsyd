import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Color palette: darker for firm/dispatchable; brighter for renewables/hydro
const TYPE_COLORS = {
  'Nuclear':             '#c084fc', // violet
  'Lignite':             '#78716c', // stone (dark)
  'Hard Coal':           '#57534e', // stone (darker)
  'Fossil Gas':          '#6b7280', // gray
  'Oil':                 '#44403c', // very dark
  'Biomass':             '#4ade80', // green
  'Geothermal':          '#fb923c', // orange
  'Hydro Pumped Storage':'#38bdf8', // sky
  'Hydro Run-of-river':  '#0ea5e9', // sky darker
  'Hydro Reservoir':     '#0284c7', // blue
  'Other Renewable':     '#a3e635', // lime
  'Solar':               '#fbbf24', // amber
  'Waste':               '#a8a29e', // stone light
  'Wind Offshore':       '#22d3ee', // cyan
  'Wind Onshore':        '#67e8f9', // cyan lighter
  'Other':               '#374151', // gray dark
}

const DEFAULT_COLOR = '#64748b'

export default function GenerationMixPanel({ zone = 'DE_LU' }) {
  const url = `${API}/power/generation-mix?days=30&zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone] })

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">GENERATION MIX // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest
  const types = data?.types ?? []

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
      title={`GENERATION MIX · ${zoneLabel}`}
      info="Full ENTSO-E A75 generation breakdown by production type (daily mean MW). Covers nuclear, coal, gas, hydro, biomass, wind, solar and more. Source: ENTSO-E Transparency Platform, processType A16."
      collapsible
      defaultCollapsed
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
                  style={{ color: TYPE_COLORS[topType] ?? DEFAULT_COLOR }}
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
                    contentStyle={CHART_TOOLTIP_STYLE}
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
                      stroke={TYPE_COLORS[type] ?? DEFAULT_COLOR}
                      fill={TYPE_COLORS[type] ?? DEFAULT_COLOR}
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
                  <span key={type} style={{ color: TYPE_COLORS[type] ?? DEFAULT_COLOR }}>
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
