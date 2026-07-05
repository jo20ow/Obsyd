import { useMemo, useState } from 'react'
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

function isoDaysAgo(n) {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - n)
  return d.toISOString().slice(0, 10)
}

const RANGES = [
  { key: '2y', label: '2Y', days: 731 },
  { key: '3y', label: '3Y', days: 1096 },
  { key: '5y', label: '5Y', days: 1826 },
]

export default function TrendsPanel({ zone = 'DE_LU' }) {
  const [range, setRange] = useState('3y')
  const days = RANGES.find((r) => r.key === range)?.days ?? 1096
  const start = useMemo(() => isoDaysAgo(days), [days])

  // Negative-price hours need hourly price; renewables share is fine from daily load/residual.
  const priceUrl = `${API}/v1/series?series=price.dayahead&zone=${zone}&start=${start}&resolution=hourly`
  const loadUrl = `${API}/v1/series?series=load.actual&zone=${zone}&start=${start}&resolution=daily`
  const resUrl = `${API}/v1/series?series=residual.actual&zone=${zone}&start=${start}&resolution=daily`
  const { data: price, loading: l1 } = useFetchWithError(priceUrl, { deps: [zone, start] })
  const { data: load, loading: l2 } = useFetchWithError(loadUrl, { deps: [zone, start] })
  const { data: res, loading: l3 } = useFetchWithError(resUrl, { deps: [zone, start] })
  const loading = l1 || l2 || l3

  const { rows, totalNeg } = useMemo(() => {
    // Negative-price hours + avg price per month (hourly).
    const neg = {}
    const psum = {}
    const pcnt = {}
    for (const p of price?.data || []) {
      const m = String(p.datetime_utc).slice(0, 7)
      if (p.value < 0) neg[m] = (neg[m] || 0) + 1
      psum[m] = (psum[m] || 0) + p.value
      pcnt[m] = (pcnt[m] || 0) + 1
    }
    // Renewables share per month (daily): (load − residual) / load.
    const loadByT = new Map((load?.data || []).map((d) => [d.date, d.value]))
    const shareSum = {}
    const shareCnt = {}
    for (const r of res?.data || []) {
      const l = loadByT.get(r.date)
      if (l && l > 0 && r.value != null) {
        const share = Math.max(0, Math.min(1, (l - r.value) / l))
        const m = String(r.date).slice(0, 7)
        shareSum[m] = (shareSum[m] || 0) + share
        shareCnt[m] = (shareCnt[m] || 0) + 1
      }
    }
    const months = [...new Set([...Object.keys(pcnt), ...Object.keys(shareCnt)])].sort()
    const rows = months.map((m) => ({
      t: m,
      negHours: neg[m] || 0,
      avgPrice: pcnt[m] ? Math.round((psum[m] / pcnt[m]) * 10) / 10 : null,
      renewShare: shareCnt[m] ? Math.round((shareSum[m] / shareCnt[m]) * 1000) / 10 : null,
    }))
    const totalNeg = Object.values(neg).reduce((a, b) => a + b, 0)
    return { rows, totalNeg }
  }, [price, load, res])

  if (!loading && rows.length === 0) return null

  return (
    <Panel
      id="trends"
      title="TRENDS · negative-price hours & renewables"
      info="Per-month negative-price hours (bars, left axis) and renewables share = (load − residual)/load (line, right axis) — the energy-transition headline metrics over time. Descriptive, from the official record."
      collapsible
      defaultCollapsed
    >
      <div className="flex items-center gap-1 px-4 pt-3">
        {RANGES.map((r) => (
          <button key={r.key} onClick={() => setRange(r.key)}
            className={`font-mono text-[9px] px-2 py-0.5 rounded border ${range === r.key ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
            {r.label}
          </button>
        ))}
      </div>
      {rows.length > 0 && (
        <div className="px-4 pt-2">
          <PanelTakeaway>{`${totalNeg} negative-price hours over the period; renewables share trends on the right axis.`}</PanelTakeaway>
        </div>
      )}
      {loading && <div className="px-4 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {rows.length > 0 && (
        <div className="px-2 pt-2 pb-1">
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={rows} margin={{ top: 5, right: 6, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" tick={{ fontSize: 8, fill: '#737373' }} minTickGap={30} />
              <YAxis yAxisId="l" tick={{ fontSize: 8, fill: '#737373' }} width={30} />
              <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 8, fill: '#737373' }} width={30} unit="%" domain={[0, 100]} />
              <Tooltip {...CHART_TOOLTIP_STYLE}
                formatter={(v, n) => [n === 'renewShare' ? `${v}%` : v, n === 'negHours' ? 'Neg-price h' : 'Renewables']} />
              <Legend wrapperStyle={{ fontSize: 8, fontFamily: 'monospace' }} iconSize={7} />
              <Bar yAxisId="l" dataKey="negHours" name="Neg-price h" fill="#f87171" fillOpacity={0.7} />
              <Line yAxisId="r" type="monotone" dataKey="renewShare" name="Renewables %" stroke="#4ade80" dot={false} strokeWidth={1.5} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  )
}
