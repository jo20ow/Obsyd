import { useMemo } from 'react'
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Same fuel palette as GenMixHistoryPanel.
const FUEL_COLORS = {
  Solar: '#facc15', 'Wind Onshore': '#22d3ee', 'Wind Offshore': '#0ea5e9',
  'Fossil Gas': '#f97316', 'Hard Coal': '#78716c', 'Fossil Brown coal/Lignite': '#92400e',
  Nuclear: '#a855f7', 'Hydro Water Reservoir': '#3b82f6', 'Hydro Run-of-river and poundage': '#60a5fa',
  'Hydro Pumped Storage': '#2563eb', Biomass: '#84cc16', 'Fossil Oil': '#525252',
  Waste: '#a3a3a3', Geothermal: '#ef4444', Other: '#9ca3af', 'Other renewable': '#4ade80',
}
const CYCLE = ['#f472b6', '#fb923c', '#34d399', '#818cf8', '#e879f9', '#fbbf24']

// Compact stacked generation mix per zone for the Live grid (Fuel Mix section).
// Daily resolution, floored to 90d so the stack has shape; GW.
export default function MiniMixCard({ title, zone }) {
  const { range } = useViewState()
  const start = rangeStart(range, 90)
  const url = `${API}/v1/genmix?zone=${zone}&start=${start}&resolution=daily`
  const { data: resp, loading } = useFetchWithError(url, { deps: [zone, start] })

  const { chart, fuels } = useMemo(() => {
    const f = resp?.fuels || []
    const rows = (resp?.data || []).map((row) => {
      const o = { t: row.t }
      for (const fuel of f) if (row[fuel] != null) o[fuel] = Math.round((row[fuel] / 1000) * 10) / 10
      return o
    })
    return { chart: rows, fuels: f }
  }, [resp])
  const colorFor = (fuel, i) => FUEL_COLORS[fuel] || CYCLE[i % CYCLE.length]

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50">
        <span className="font-mono text-[11px] font-medium text-neutral-300 truncate">{title}</span>
        <a
          href={`${url}&format=csv`}
          title="Download CSV"
          className="font-mono text-[9px] border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors shrink-0"
        >
          ↓
        </a>
      </div>
      {loading && <div className="px-3 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {chart.length > 0 && (
        <div className="px-1 py-2">
          <ResponsiveContainer width="100%" height={120}>
            <AreaChart data={chart} margin={{ top: 5, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" tick={{ fontSize: 8, fill: '#737373' }} minTickGap={30} />
              <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={30} />
              <Tooltip {...CHART_TOOLTIP_STYLE} formatter={(v, n) => [`${Number(v).toFixed(1)} GW`, n]} />
              {fuels.map((f, i) => (
                <Area key={f} type="monotone" dataKey={f} stackId="1" stroke={colorFor(f, i)} fill={colorFor(f, i)} fillOpacity={0.6} strokeWidth={0.5} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
      {!loading && chart.length === 0 && (
        <div className="px-3 py-8 text-center font-mono text-[10px] text-neutral-600">No data for this zone.</div>
      )}
    </div>
  )
}
