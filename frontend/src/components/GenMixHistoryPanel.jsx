import { useMemo } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Readable ENTSO-E fuel label → colour; anything unmapped falls back to the cycle.
const FUEL_COLORS = {
  Solar: '#facc15', 'Wind Onshore': '#22d3ee', 'Wind Offshore': '#0ea5e9',
  'Fossil Gas': '#f97316', 'Hard Coal': '#78716c', 'Fossil Brown coal/Lignite': '#92400e',
  Nuclear: '#a855f7', 'Hydro Water Reservoir': '#3b82f6', 'Hydro Run-of-river and poundage': '#60a5fa',
  'Hydro Pumped Storage': '#2563eb', Biomass: '#84cc16', 'Fossil Oil': '#525252',
  Waste: '#a3a3a3', Geothermal: '#ef4444', Other: '#9ca3af', 'Other renewable': '#4ade80',
}
const CYCLE = ['#f472b6', '#fb923c', '#34d399', '#818cf8', '#e879f9', '#fbbf24']

export default function GenMixHistoryPanel({ zone = 'DE_LU' }) {
  const { range } = useViewState()
  // Monthly buckets need >= 1y to be meaningful, so a short global range floors here.
  const start = rangeStart(range, 365)

  const url = `${API}/v1/genmix?zone=${zone}&start=${start}&resolution=monthly`
  const { data: resp, loading } = useFetchWithError(url, { deps: [zone, start] })

  const colorFor = (fuel, i) => FUEL_COLORS[fuel] || CYCLE[i % CYCLE.length]
  // Convert MW → GW for readability without mutating source rows.
  const { chart, fuels } = useMemo(() => {
    const f = resp?.fuels || []
    const chart = (resp?.data || []).map((row) => {
      const o = { t: row.t }
      for (const fuel of f) if (row[fuel] != null) o[fuel] = Math.round((row[fuel] / 1000) * 10) / 10
      return o
    })
    return { chart, fuels: f }
  }, [resp])

  if (!loading && (!resp?.available || fuels.length === 0)) return null

  return (
    <Panel
      id="genmix-history"
      title="GENERATION MIX · over time"
      info="Monthly-mean generation by fuel (ENTSO-E A75), stacked over years — the energy transition per zone. GW. Descriptive, from the official record."
      collapsible
      downloadUrl={`${url}&format=csv`}
      headerRight={<span className="font-mono text-[9px] text-neutral-600">GW · ≥1Y</span>}
    >
      {loading && <div className="px-4 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {chart.length > 0 && (
        <div className="px-2 pt-2 pb-1">
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={chart} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" tick={{ fontSize: 8, fill: '#737373' }} minTickGap={30} />
              <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={34} unit="" />
              <Tooltip {...CHART_TOOLTIP_STYLE} formatter={(v, n) => [`${Number(v).toFixed(1)} GW`, n]} />
              <Legend wrapperStyle={{ fontSize: 8, fontFamily: 'monospace' }} iconSize={7} />
              {fuels.map((f, i) => (
                <Area key={f} type="monotone" dataKey={f} stackId="1" stroke={colorFor(f, i)}
                  fill={colorFor(f, i)} fillOpacity={0.65} strokeWidth={0.5} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  )
}
