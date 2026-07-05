import { useMemo, useState } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'
const MAX_POINTS = 2000

function isoDaysAgo(n) {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - n)
  return d.toISOString().slice(0, 10)
}

const RANGES = [
  { key: '90d', label: '90D', days: 90 },
  { key: '1y', label: '1Y', days: 365 },
  { key: '5y', label: '5Y', days: 1826 },
]

const METRICS = {
  price: { key: 'price.dayahead', label: 'Day-ahead price', unit: '€/MWh', scale: 1, color: '#22d3ee' },
  residual: { key: 'residual.actual', label: 'Residual load', unit: 'GW', scale: 1 / 1000, color: '#a78bfa' },
}

export default function DurationCurvePanel({ zone = 'DE_LU' }) {
  const [metric, setMetric] = useState('price')
  const [range, setRange] = useState('1y')
  const m = METRICS[metric]
  const days = RANGES.find((r) => r.key === range)?.days ?? 365
  const start = useMemo(() => isoDaysAgo(days), [days])

  const url = `${API}/v1/series?series=${m.key}&zone=${zone}&start=${start}&resolution=hourly`
  const { data: resp, loading } = useFetchWithError(url, { deps: [m.key, zone, start] })

  const { curve, stats } = useMemo(() => {
    const vals = (resp?.data || []).map((p) => p.value * m.scale).filter((v) => v != null && !Number.isNaN(v))
    vals.sort((a, b) => b - a) // descending
    const n = vals.length
    if (!n) return { curve: [], stats: null }
    const step = Math.max(1, Math.ceil(n / MAX_POINTS))
    const curve = []
    for (let i = 0; i < n; i += step) curve.push({ pct: Math.round((i / n) * 1000) / 10, v: Math.round(vals[i] * 100) / 100 })
    const median = vals[Math.floor(n / 2)]
    const negHours = metric === 'price' ? vals.filter((v) => v < 0).length : null
    return { curve, stats: { n, max: vals[0], min: vals[n - 1], median, negHours } }
  }, [resp, m.scale, metric])

  if (!loading && (!resp?.available || !stats)) return null

  return (
    <Panel
      id="duration-curve"
      title={`DURATION CURVE · ${m.label}`}
      info="Every hour in the range sorted from highest to lowest, plotted against the % of hours. Reads 'how many hours sit above a given level' — the classic load/price-duration curve. Descriptive, from the official hourly record."
      collapsible
      headerRight={<span className="font-mono text-[9px] text-neutral-600">{stats ? `${stats.n} h` : ''}</span>}
    >
      <div className="flex flex-wrap items-center gap-2 px-4 pt-3">
        <div className="flex items-center gap-1">
          {Object.entries(METRICS).map(([k, v]) => (
            <button key={k} onClick={() => setMetric(k)}
              className={`font-mono text-[9px] px-2 py-0.5 rounded border ${metric === k ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {v.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {RANGES.map((r) => (
            <button key={r.key} onClick={() => setRange(r.key)}
              className={`font-mono text-[9px] px-2 py-0.5 rounded border ${range === r.key ? 'text-violet-300 border-violet-400/40 bg-violet-400/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {stats && (
        <div className="px-4 pt-2">
          <PanelTakeaway>
            {`Peak ${stats.max.toFixed(1)} ${m.unit}, median ${stats.median.toFixed(1)}, low ${stats.min.toFixed(1)}.`}
            {metric === 'price' && stats.negHours ? ` ${stats.negHours} h below €0 (renewable oversupply).` : ''}
          </PanelTakeaway>
        </div>
      )}
      {loading && <div className="px-4 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {curve.length > 0 && (
        <div className="px-2 pt-2 pb-1">
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={curve} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="pct" tick={{ fontSize: 8, fill: '#737373' }} unit="%" type="number" domain={[0, 100]} />
              <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={40} />
              {metric === 'price' && <ReferenceLine y={0} stroke="#444" />}
              <Tooltip {...CHART_TOOLTIP_STYLE}
                labelFormatter={(p) => `${p}% of hours`}
                formatter={(v) => [`${Number(v).toFixed(1)} ${m.unit}`, m.label]} />
              <Area type="monotone" dataKey="v" stroke={m.color} fill={m.color} fillOpacity={0.08} strokeWidth={1.5} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
          <div className="px-2 font-mono text-[8px] text-neutral-700">x = % of hours in range (sorted high→low)</div>
        </div>
      )}
    </Panel>
  )
}
