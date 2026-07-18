import { useMemo } from 'react'
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_PROPS } from '../utils/chart'

const API = '/api'

export default function TrendsPanel({ zone = 'DE_LU' }) {
  const { range } = useViewState()
  // Monthly aggregates need >= 1y to be meaningful, so a short global range floors here.
  const start = rangeStart(range, 365)

  // Negative-price hours need hourly price; renewables share is fine from daily load/residual.
  const priceUrl = `${API}/v1/series?series=price.dayahead&zone=${zone}&start=${start}&resolution=hourly`
  const loadUrl = `${API}/v1/series?series=load.actual&zone=${zone}&start=${start}&resolution=daily`
  const resUrl = `${API}/v1/series?series=residual.actual&zone=${zone}&start=${start}&resolution=daily`
  const { data: price, loading: l1, error: e1 } = useFetchWithError(priceUrl, { deps: [zone, start] })
  const { data: load, loading: l2, error: e2 } = useFetchWithError(loadUrl, { deps: [zone, start] })
  const { data: res, loading: l3, error: e3 } = useFetchWithError(resUrl, { deps: [zone, start] })
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

  if ((e1 || e2 || e3) && rows.length === 0) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">TRENDS // FETCH ERROR</div>
      </div>
    )
  }
  if (!loading && rows.length === 0) return null

  return (
    <Panel
      id="trends"
      title="TRENDS · negative-price hours & renewables"
      info="Per-month negative-price hours (bars, left axis) and renewables share = (load − residual)/load (line, right axis) — the energy-transition headline metrics over time. Descriptive, from the official record."
      collapsible
      defaultCollapsed
      downloadUrl={`${priceUrl}&format=csv`}
    >
      {rows.length > 0 && (
        <div className="px-4 pt-3">
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
              <Tooltip {...CHART_TOOLTIP_PROPS}
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
