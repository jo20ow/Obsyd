import { useState, useEffect, useCallback } from 'react'
import Panel from './Panel'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts'

const API = '/api'

const CHOKEPOINTS = [
  { key: 'hormuz', label: 'HORMUZ' },
  { key: 'suez', label: 'SUEZ' },
  { key: 'cape', label: 'CAPE' },
  { key: 'malacca', label: 'MALACCA' },
]

const TIMEFRAMES = [
  { label: '90D', days: 90 },
  { label: '180D', days: 180 },
  { label: '1Y', days: 365 },
]

function formatDateShort(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00Z')
  return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="border border-border bg-surface px-3 py-2 font-mono text-[10px]">
      <div className="text-neutral-500 mb-1">{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ color: p.color }}>
          {p.name}: {p.value}{p.dataKey === 'brent' ? ' $/bbl' : ''}
        </div>
      ))}
    </div>
  )
}

export default function TransitChart() {
  const [selected, setSelected] = useState('hormuz')
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[1])
  const [history, setHistory] = useState(null)
  const [oilPrices, setOilPrices] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/prices/oil?days=365`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d?.series) setOilPrices(d.series) })
      .catch(() => {})
  }, [])

  const fetchHistory = useCallback((cp, days) => {
    setLoading(true)
    fetch(`${API}/portwatch/chokepoints/${cp}/history?days=${days}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setHistory(d.history) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    fetchHistory(selected, timeframe.days)
  }, [selected, timeframe, fetchHistory])

  const brentMap = {}
  if (oilPrices?.DCOILBRENTEU?.data) {
    for (const p of oilPrices.DCOILBRENTEU.data) {
      brentMap[p.date] = p.value
    }
  }

  // Filter incomplete tail data
  const pwHistory = (history || []).filter((d) => d.source !== 'ais')
  const chartData = pwHistory.map((d) => ({
    date: d.date,
    tankers: d.n_tanker,
    brent: brentMap[d.date] ?? null,
  }))

  const hasBrent = chartData.some((d) => d.brent !== null)

  return (
    <Panel
      id="transit-chart"
      title={`${selected.toUpperCase()} TRANSIT HISTORY`}
      info="PortWatch tanker transit count overlaid with Brent crude price. Source: IMF PortWatch, 3-5 day delay."
      headerRight={
        <div className="flex items-center gap-1">
          {CHOKEPOINTS.map((cp) => (
            <button
              key={cp.key}
              onClick={() => setSelected(cp.key)}
              className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${
                selected === cp.key
                  ? 'bg-cyan-glow/20 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {cp.label}
            </button>
          ))}
          <span className="text-neutral-800 mx-1">|</span>
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.label}
              onClick={() => setTimeframe(tf)}
              className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${
                timeframe.label === tf.label
                  ? 'bg-cyan-glow/20 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {tf.label}
            </button>
          ))}
        </div>
      }
    >
      <div className="px-4 py-3">
        {loading && (
          <div className="h-[240px] flex items-center justify-center font-mono text-[10px] text-neutral-600 animate-pulse">
            LOADING ...
          </div>
        )}
        {!loading && chartData.length === 0 && (
          <div className="h-[240px] flex items-center justify-center font-mono text-[10px] text-neutral-600">
            No transit data available for {selected.toUpperCase()}
          </div>
        )}
        {!loading && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chartData} margin={{ top: 5, right: hasBrent ? 45 : 5, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 9, fill: '#555', fontFamily: 'monospace' }}
                tickFormatter={formatDateShort}
                interval="preserveStartEnd"
                minTickGap={60}
              />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 9, fill: '#00ff9d88', fontFamily: 'monospace' }}
                width={35}
                label={{ value: 'Tankers', angle: -90, position: 'insideLeft', style: { fontSize: 9, fill: '#555', fontFamily: 'monospace' } }}
              />
              {hasBrent && (
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  tick={{ fontSize: 9, fill: '#ff884488', fontFamily: 'monospace' }}
                  width={40}
                  tickFormatter={(v) => `$${v}`}
                />
              )}
              <Tooltip content={<CustomTooltip />} />
              <Legend
                wrapperStyle={{ fontSize: 9, fontFamily: 'monospace' }}
                iconSize={8}
              />
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="tankers"
                name="Tanker Count"
                stroke="#00ff9d"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 3 }}
                connectNulls={false}
              />
              {hasBrent && (
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="brent"
                  name="Brent"
                  stroke="#ff8844"
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 3 }}
                  connectNulls
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </Panel>
  )
}
