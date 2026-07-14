import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeDays } from '../utils/ranges'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

function Stat({ label, value }) {
  return (
    <div className="flex items-center justify-between font-mono text-[10px]">
      <span className="text-neutral-600">{label}</span>
      <span className="text-neutral-300">{value == null ? '—' : `${Math.round(value).toLocaleString()} GWh`}</span>
    </div>
  )
}

// Merge demand + power-burn rows by date into [{date, power, heat, industrial, total}].
function mergeRows(demand, power) {
  const byDate = new Map()
  // Demand is the authoritative source — it defines which dates the modeled
  // total exists for. Seed from demand only.
  for (const r of demand?.data ?? []) {
    byDate.set(r.date, { date: r.date, heat: r.heat_gwh ?? 0, industrial: r.industrial_gwh ?? 0, power: 0 })
  }
  // Annotate power burn onto demand-covered dates only; ignore power-only dates
  // so the chart never shows a misleading power-only bar with heat/industrial=0.
  for (const r of power?.data ?? []) {
    const row = byDate.get(r.date)
    if (row) row.power = r.implied_gas_gwh ?? 0
  }
  const rows = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))
  for (const r of rows) r.total = r.power + r.heat + r.industrial
  return rows
}

export default function GasDemandPanel() {
  const { range } = useViewState()
  const demand = useFetchWithError(`${API}/gas/demand?days=${rangeDays(range)}`, { deps: [range] })
  const power = useFetchWithError(`${API}/gas/power-burn?days=${rangeDays(range)}`, { deps: [range] })

  const loading = demand.loading || power.loading
  const error = demand.error // demand is the required source; power may be unavailable

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS DEMAND // FETCH ERROR</div>
      </div>
    )
  if (!demand.data?.available && !loading) return null

  const rows = mergeRows(demand.data, power.data)
  const latest = rows[rows.length - 1]
  const modelVersion = demand.data?.data?.[demand.data.data.length - 1]?.model_version

  return (
    <Panel
      id="gas-demand"
      freshness={demand.data}
      title="EU GAS DEMAND"
      info="Modeled EU gas demand = gas-fired power burn (ENTSO-E) + HDD-driven heating + flat industrial baseline. model_version flags whether power burn is separated."
      collapsible
      headerRight={latest && <span className="font-mono text-[10px] text-cyan-glow font-bold">{Math.round(latest.total).toLocaleString()}</span>}
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading demand…</div>
      )}
      {!loading && demand.data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-2 mb-2">
              <span className="font-mono text-3xl font-bold text-cyan-glow">{Math.round(latest.total).toLocaleString()}</span>
              <span className="font-mono text-[10px] text-neutral-600">GWh/d total</span>
            </div>
            <div className="space-y-1">
              <Stat label="power burn" value={latest.power} />
              <Stat label="heating (HDD)" value={latest.heat} />
              <Stat label="industrial" value={latest.industrial} />
            </div>
            {modelVersion && (
              // The fitted coefficients are provenance, not a headline: the panel used to print
              // "model: v1+power;a=139307;b=744.0;n=41" at the reader, which reads as a debug
              // leak. Name the model, keep the parameters one hover away.
              <div
                className="font-mono text-[8px] text-neutral-700 mt-2 truncate"
                title={`Fitted demand model — ${modelVersion}`}
              >
                model: {String(modelVersion).split(';')[0]}
              </div>
            )}
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={28} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v, name) => [`${Math.round(v).toLocaleString()} GWh`, name]} labelFormatter={fmtDate} />
                  <Area type="monotone" dataKey="power" stackId="1" stroke="#a78bfa" fill="#a78bfa" fillOpacity={0.15} strokeWidth={1} dot={false} />
                  <Area type="monotone" dataKey="heat" stackId="1" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.12} strokeWidth={1} dot={false} />
                  <Area type="monotone" dataKey="industrial" stackId="1" stroke="#64748b" fill="#64748b" fillOpacity={0.12} strokeWidth={1} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
