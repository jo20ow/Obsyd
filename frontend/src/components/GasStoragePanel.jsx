import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'

const API = '/api'

function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const TOOLTIP_STYLE = { background: '#0a0a12', border: '1px solid #2a2a3a', fontFamily: 'monospace', fontSize: 10 }

export default function GasStoragePanel() {
  const { data, loading, error } = useFetchWithError(`${API}/gas/storage?days=120`)

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS STORAGE // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = rows[rows.length - 1]
  const fill = latest?.fill_pct
  const net = latest ? (latest.injection_gwh || 0) - (latest.withdrawal_gwh || 0) : 0

  return (
    <Panel
      id="gas-storage"
      title="EU GAS STORAGE · AGSI"
      info="EU gas in storage (AGSI/GIE). Fill % of working capacity plus daily injection/withdrawal. Free, official redistributable data."
      collapsible
      headerRight={fill != null && <span className="font-mono text-[10px] text-cyan-glow font-bold">{fill.toFixed(1)}%</span>}
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading storage…</div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-3xl font-bold text-cyan-glow">{fill?.toFixed(1)}%</span>
              <span className="font-mono text-[10px] text-neutral-600">{latest.stock_twh?.toFixed(1)} TWh</span>
            </div>
            <div className={`font-mono text-[10px] mt-1 ${net >= 0 ? 'text-green-glow' : 'text-orange-400'}`}>
              {net >= 0 ? '▲ injecting' : '▼ withdrawing'} {Math.abs(net).toFixed(0)} GWh/d
            </div>
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={70}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={24} domain={[0, 100]} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [`${Number(v).toFixed(1)}%`, 'fill']} labelFormatter={fmtDate} />
                  <Area type="monotone" dataKey="fill_pct" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.06} strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
