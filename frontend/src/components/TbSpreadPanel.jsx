import { useMemo, useState } from 'react'
import {
  ResponsiveContainer, ComposedChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { fmtTs, CHART_TOOLTIP_PROPS, useChartTheme } from '../utils/chart'

const API = '/api'

const WINDOWS = [
  { n: 1, label: 'TB1', hint: '1h system' },
  { n: 2, label: 'TB2', hint: '2h system' },
  { n: 4, label: 'TB4', hint: '4h system' },
]

// 30-day trailing mean smooths the daily saw without hiding it — the daily
// dots stay on the chart, the line is labelled as the mean.
function rollingMean(rows, window = 30) {
  const out = []
  let sum = 0
  const q = []
  for (const r of rows) {
    q.push(r.v); sum += r.v
    if (q.length > window) sum -= q.shift()
    out.push({ ...r, mean: q.length >= Math.min(window, 7) ? sum / q.length : null })
  }
  return out
}

/**
 * Daily day-ahead top-bottom spread (TB1/TB2/TB4) — a PRICE-SPREAD statistic,
 * deliberately never framed as battery revenue (see the info text: real
 * assets earn across markets this number does not see, in both directions).
 * Reads the stored spread.tb* series, so panel and CSV export agree.
 */
export default function TbSpreadPanel({ zone = 'DE_LU' }) {
  const [n, setN] = useState(2)
  const ct = useChartTheme()
  const { range } = useViewState()
  const start = rangeStart(range, 90)

  const url = `${API}/v1/series?series=spread.tb${n}&zone=${zone}&start=${start}&resolution=hourly`
  const { data: resp, loading, error } = useFetchWithError(url, { deps: [n, zone, start] })

  const { rows, stats } = useMemo(() => {
    // /api/v1/series rows carry `datetime_utc` (not the desk endpoints' ts_utc)
    const pts = (resp?.data || [])
      .filter((p) => p.value != null)
      .map((p) => ({ x: p.datetime_utc, v: p.value }))
    if (!pts.length) return { rows: [], stats: null }
    const total = pts.reduce((s, p) => s + p.v, 0)
    return {
      rows: rollingMean(pts),
      stats: { days: pts.length, mean: total / pts.length, total },
    }
  }, [resp])

  if (error && !resp) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">TOP-BOTTOM SPREAD // FETCH ERROR</div>
      </div>
    )
  }
  if (!loading && (!resp?.available || !stats)) return null

  const w = WINDOWS.find((x) => x.n === n)
  return (
    <Panel
      source="Derived from ENTSO-E 12.1.D day-ahead prices · TB convention: Modo Energy"
      id="tb-spread"
      title="DAY-AHEAD TOP-BOTTOM SPREAD (TB)"
      info={`TB${n} = the sum of the day's ${n} highest hourly day-ahead prices minus the sum of its ${n} lowest — what a ${n}-hour storage system cycling once per day would have captured IN THE DAY-AHEAD AUCTION ALONE, per MW, with perfect knowledge of the published prices. A price-spread statistic, not a revenue benchmark: real systems also earn in intraday, balancing and ancillary markets (GB 2h systems earned ~1.4× TB2 in 2025), and real day-ahead trading does not capture the full spread either. No efficiency applied; UTC days; days under 20 priced hours skipped. Descriptive arithmetic on published prices.`}
      collapsible
      downloadUrl={`${url}&format=csv`}
      headerRight={<span className="font-mono text-[9px] text-neutral-600">{stats ? `${stats.days} days` : ''}</span>}
    >
      <div className="flex flex-wrap items-center gap-2 px-4 pt-3">
        <div className="flex items-center gap-1">
          {WINDOWS.map((x) => (
            <button key={x.n} onClick={() => setN(x.n)}
              title={x.hint}
              className={`font-mono text-[9px] px-2 py-0.5 rounded border ${n === x.n ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {x.label}
            </button>
          ))}
        </div>
        <span className="font-mono text-[9px] text-neutral-600">{w.hint} · 1 cycle/day · €/MW·day</span>
      </div>

      {stats && (
        <div className="px-4 pt-2">
          <PanelTakeaway>
            {`${w.label} averaged €${stats.mean.toFixed(0)}/MW·day over ${stats.days} days (Σ €${(stats.total / 1000).toFixed(1)}k/MW). Day-ahead auction only — not asset revenue.`}
          </PanelTakeaway>
        </div>
      )}
      {loading && <div className="px-4 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {rows.length > 0 && (
        <div className="px-2 pt-2 pb-1">
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={rows} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid {...ct.grid} />
              <XAxis dataKey="x" tickFormatter={fmtTs} tick={ct.tick} minTickGap={70} />
              <YAxis tick={ct.tick} width={44} />
              <Tooltip {...CHART_TOOLTIP_PROPS}
                labelFormatter={fmtTs}
                formatter={(v, name) => [`€${Number(v).toFixed(0)}/MW·day`,
                  name === 'mean' ? '30d mean' : `${w.label} daily`]} />
              <Line type="monotone" dataKey="v" stroke={ct.ink} strokeWidth={0.6} dot={false}
                opacity={0.45} isAnimationActive={false} />
              <Line type="monotone" dataKey="mean" stroke={ct.accent} strokeWidth={1.8} dot={false}
                connectNulls={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
          <div className="px-2 font-mono text-[8px] text-neutral-700">
            thin = daily {w.label} · bold = 30-day trailing mean
          </div>
        </div>
      )}
    </Panel>
  )
}
