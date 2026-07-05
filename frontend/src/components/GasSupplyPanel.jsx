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

export default function GasSupplyPanel() {
  const { range } = useViewState()
  const { data, loading, error } = useFetchWithError(`${API}/gas/supply?days=${rangeDays(range)}`, { deps: [range] })

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS SUPPLY // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = rows[rows.length - 1]

  return (
    <Panel
      id="gas-supply"
      title="EU GAS SUPPLY · ENTSOG"
      info="Daily EU gas supply (GWh/d) from ENTSOG physical flows: pipeline imports + LNG send-out + net UK interconnector. Free, official data."
      collapsible
      headerRight={latest && <span className="font-mono text-[10px] text-cyan-glow font-bold">{Math.round(latest.supply_gwh).toLocaleString()}</span>}
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading supply…</div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-2 mb-2">
              <span className="font-mono text-3xl font-bold text-cyan-glow">{Math.round(latest.supply_gwh).toLocaleString()}</span>
              <span className="font-mono text-[10px] text-neutral-600">GWh/d total</span>
            </div>
            <div className="space-y-1">
              <Stat label="pipeline" value={latest.pipeline_gwh} />
              <Stat label="LNG send-out" value={latest.lng_gwh} />
              <Stat label="net UK" value={latest.uk_net_gwh} />
            </div>
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={28} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v) => [`${Math.round(v).toLocaleString()} GWh`, 'supply']} labelFormatter={fmtDate} />
                  <Area type="monotone" dataKey="supply_gwh" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.06} strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
