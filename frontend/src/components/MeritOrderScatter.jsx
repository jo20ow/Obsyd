import { useMemo } from 'react'
import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_PROPS } from '../utils/chart'

const API = '/api'
const MAX_POINTS = 4000

export default function MeritOrderScatter({ zone = 'DE_LU' }) {
  const { range } = useViewState()
  const start = rangeStart(range)

  const priceUrl = `${API}/v1/series?series=price.dayahead&zone=${zone}&start=${start}&resolution=hourly`
  const resUrl = `${API}/v1/series?series=residual.actual&zone=${zone}&start=${start}&resolution=hourly`
  const { data: priceResp, loading: l1 } = useFetchWithError(priceUrl, { deps: [zone, start] })
  const { data: resResp, loading: l2 } = useFetchWithError(resUrl, { deps: [zone, start] })
  const loading = l1 || l2

  const { points, corr } = useMemo(() => {
    const pByT = new Map((priceResp?.data || []).map((p) => [p.datetime_utc, p.value]))
    const merged = []
    for (const r of resResp?.data || []) {
      const price = pByT.get(r.datetime_utc)
      if (price != null && r.value != null) merged.push({ x: r.value / 1000, y: price })
    }
    const n = merged.length
    if (!n) return { points: [], corr: null }
    // Pearson correlation (residual vs price) before downsampling.
    const mx = merged.reduce((s, p) => s + p.x, 0) / n
    const my = merged.reduce((s, p) => s + p.y, 0) / n
    let sxy = 0, sxx = 0, syy = 0
    for (const p of merged) { const dx = p.x - mx, dy = p.y - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy }
    const corr = sxx > 0 && syy > 0 ? sxy / Math.sqrt(sxx * syy) : null
    const step = Math.max(1, Math.ceil(n / MAX_POINTS))
    const points = merged.filter((_, i) => i % step === 0)
    return { points, corr }
  }, [priceResp, resResp])

  if (!loading && points.length === 0) return null

  return (
    <Panel
      id="merit-order"
      title="MERIT ORDER · price vs residual load"
      info="Each point is one hour: residual load (load − wind − solar, x) vs the day-ahead price (y). The upward cloud is the merit-order curve — as residual load rises, pricier plants set the price. Descriptive, from the official hourly record."
      collapsible
      defaultCollapsed
    >
      {corr != null && (
        <div className="px-4 pt-2">
          <PanelTakeaway>
            {`Residual load and price move together (r = ${corr.toFixed(2)}) — the merit-order signature: more residual demand → higher-cost plants set the price.`}
          </PanelTakeaway>
        </div>
      )}
      {loading && <div className="px-4 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {points.length > 0 && (
        <div className="px-2 pt-2 pb-1">
          <ResponsiveContainer width="100%" height={220}>
            <ScatterChart margin={{ top: 5, right: 12, left: 0, bottom: 12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis type="number" dataKey="x" name="Residual" unit=" GW" tick={{ fontSize: 8, fill: '#737373' }}
                label={{ value: 'Residual load (GW)', position: 'insideBottom', offset: -4, fontSize: 8, fill: '#737373' }} />
              <YAxis type="number" dataKey="y" name="Price" unit=" €" tick={{ fontSize: 8, fill: '#737373' }} width={40} />
              <ReferenceLine y={0} stroke="#444" />
              <Tooltip {...CHART_TOOLTIP_PROPS} cursor={{ strokeDasharray: '3 3' }}
                formatter={(v, n) => [n === 'Price' ? `${Number(v).toFixed(1)} €/MWh` : `${Number(v).toFixed(1)} GW`, n]} />
              <Scatter data={points} fill="#22d3ee" fillOpacity={0.35} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  )
}
