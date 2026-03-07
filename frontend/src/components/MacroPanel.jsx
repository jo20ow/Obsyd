import { useState, useEffect, useMemo } from 'react'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts'
import { SkeletonCard } from './Skeleton'
import Panel from './Panel'

const API = '/api'

const MACRO_CARDS = [
  { id: 'DTWEXBGS', label: 'DXY', unit: '', decimals: 2 },
  { id: 'T10Y2Y', label: '10Y-2Y', unit: '%', decimals: 2 },
  { id: 'FEDFUNDS', label: 'FED FUNDS', unit: '%', decimals: 2 },
]

const TIMEFRAMES = [
  { label: '90D', days: 90 },
  { label: '180D', days: 180 },
  { label: '1Y', days: 365 },
]

function formatDateShort(d) {
  if (!d) return ''
  const [, m, day] = d.split('-')
  return `${day}.${m}`
}

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  return (
    <div className="bg-[#0a0a0f] border border-border rounded px-3 py-2 font-mono text-[10px]">
      <div className="text-neutral-500 mb-1">{d.date}</div>
      {d.dxy != null && (
        <div className="text-cyan-glow">DXY {d.dxy.toFixed(2)}</div>
      )}
      {d.brent != null && (
        <div className="text-orange-400">BRENT ${d.brent.toFixed(2)}</div>
      )}
    </div>
  )
}

export default function MacroPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [tf, setTf] = useState(TIMEFRAMES[1])

  useEffect(() => {
    fetch(`${API}/prices/fred?limit=5000`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setData)
      .catch((e) => {
        console.error('MacroPanel fetch failed:', e)
        setError(e.message)
      })
  }, [])

  // Build chart data: DXY vs Brent by date
  const chartData = useMemo(() => {
    if (!data) return []
    const dxyMap = {}
    const brentMap = {}
    for (const r of data) {
      if (r.value == null) continue
      if (r.series_id === 'DTWEXBGS') dxyMap[r.date] = r.value
      if (r.series_id === 'DCOILBRENTEU') brentMap[r.date] = r.value
    }
    const allDates = [...new Set([...Object.keys(dxyMap), ...Object.keys(brentMap)])]
      .sort()
    const cutoff = new Date()
    cutoff.setDate(cutoff.getDate() - tf.days)
    const cutoffStr = cutoff.toISOString().slice(0, 10)
    return allDates
      .filter((d) => d >= cutoffStr)
      .map((d) => ({
        date: d,
        dxy: dxyMap[d] ?? null,
        brent: brentMap[d] ?? null,
      }))
  }, [data, tf])

  // Compute correlation between DXY and Brent (must be before early returns)
  const correlation = useMemo(() => {
    const paired = chartData.filter((d) => d.dxy != null && d.brent != null)
    if (paired.length < 20) return null
    const n = paired.length
    const sumX = paired.reduce((s, d) => s + d.dxy, 0)
    const sumY = paired.reduce((s, d) => s + d.brent, 0)
    const sumXY = paired.reduce((s, d) => s + d.dxy * d.brent, 0)
    const sumX2 = paired.reduce((s, d) => s + d.dxy * d.dxy, 0)
    const sumY2 = paired.reduce((s, d) => s + d.brent * d.brent, 0)
    const num = n * sumXY - sumX * sumY
    const den = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY))
    return den === 0 ? 0 : num / den
  }, [chartData])

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">
          MACRO // FETCH ERROR
        </div>
      </div>
    )

  if (data === null) return <SkeletonCard lines={4} />
  if (data.length === 0) return null

  return (
    <Panel id="macro" title={<>MACRO // DXY vs BRENT{correlation != null && <span className={`ml-2 ${correlation < -0.3 ? 'text-red-400' : correlation > 0.3 ? 'text-green-glow' : 'text-neutral-500'}`}>r={correlation.toFixed(2)}</span>}</>} info="DXY Dollar Index vs Brent crude with Pearson correlation. Strong dollar tends to weaken oil prices." collapsible headerRight={<div className="flex items-center gap-1">{TIMEFRAMES.map((t) => (<button key={t.label} onClick={() => setTf(t)} className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${tf.label === t.label ? 'bg-cyan-glow/20 text-cyan-glow' : 'text-neutral-600 hover:text-neutral-400'}`}>{t.label}</button>))}</div>}>
      <div className="px-4 py-3">

      {/* Chart */}
      {chartData.length > 0 && (
        <ResponsiveContainer width="100%" height={160}>
          <LineChart
            data={chartData}
            margin={{ top: 5, right: 40, bottom: 5, left: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 9, fill: '#555', fontFamily: 'monospace' }}
              tickFormatter={formatDateShort}
              interval="preserveStartEnd"
              minTickGap={50}
            />
            <YAxis
              yAxisId="dxy"
              tick={{ fontSize: 9, fill: '#00e5ff88', fontFamily: 'monospace' }}
              width={32}
              domain={['auto', 'auto']}
            />
            <YAxis
              yAxisId="brent"
              orientation="right"
              tick={{ fontSize: 9, fill: '#ff884488', fontFamily: 'monospace' }}
              width={36}
              tickFormatter={(v) => `$${v}`}
              domain={['auto', 'auto']}
            />
            <Tooltip content={<CustomTooltip />} />
            <Line
              yAxisId="dxy"
              type="monotone"
              dataKey="dxy"
              name="DXY"
              stroke="#00e5ff"
              strokeWidth={1.5}
              dot={false}
              connectNulls
            />
            <Line
              yAxisId="brent"
              type="monotone"
              dataKey="brent"
              name="Brent"
              stroke="#ff8844"
              strokeWidth={1.5}
              dot={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      )}

      {/* Macro cards */}
      <div className="grid grid-cols-3 gap-2 mt-2">
        {MACRO_CARDS.map((cfg) => {
          const rows = data
            .filter((r) => r.series_id === cfg.id && r.value != null)
            .sort((a, b) => (a.date > b.date ? -1 : 1))

          const latest = rows[0]
          const prev = rows[1]
          if (!latest) return null

          const changePct =
            prev && prev.value !== 0
              ? ((latest.value - prev.value) / prev.value) * 100
              : null

          const isYieldSpread = cfg.id === 'T10Y2Y'
          const inverted = isYieldSpread && latest.value < 0

          return (
            <div
              key={cfg.id}
              className={`border rounded px-2.5 py-1.5 ${
                inverted
                  ? 'border-red-500/30 bg-red-500/5'
                  : 'border-border bg-surface-light'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-[10px] text-neutral-500">
                  {cfg.label}
                </span>
                {changePct != null && (
                  <span
                    className={`font-mono text-[9px] ${
                      changePct >= 0 ? 'text-green-glow' : 'text-red-400'
                    }`}
                  >
                    {changePct >= 0 ? '+' : ''}
                    {changePct.toFixed(2)}%
                  </span>
                )}
              </div>
              <div className="font-mono text-sm font-bold text-cyan-glow">
                {latest.value.toFixed(cfg.decimals)}
                {cfg.unit && (
                  <span className="text-[10px] text-neutral-500 ml-0.5">
                    {cfg.unit}
                  </span>
                )}
                {inverted && (
                  <span className="text-[9px] text-red-400 ml-1">INVERTED</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
      </div>
    </Panel>
  )
}
