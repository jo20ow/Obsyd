import { useMemo, useState } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

function isoDaysAgo(n) {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - n)
  return d.toISOString().slice(0, 10)
}

const RANGES = [
  { key: '1y', label: '1Y', days: 365 },
  { key: '3y', label: '3Y', days: 1096 },
  { key: '5y', label: '5Y', days: 1826 },
]

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
  const [range, setRange] = useState('3y')
  const days = RANGES.find((r) => r.key === range)?.days ?? 1096
  const start = useMemo(() => isoDaysAgo(days), [days])

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
      headerRight={<span className="font-mono text-[9px] text-neutral-600">GW</span>}
    >
      <div className="flex items-center gap-1 px-4 pt-3">
        {RANGES.map((r) => (
          <button key={r.key} onClick={() => setRange(r.key)}
            className={`font-mono text-[9px] px-2 py-0.5 rounded border ${range === r.key ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
            {r.label}
          </button>
        ))}
      </div>
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
