import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeDays } from '../utils/ranges'
import {
  ResponsiveContainer, AreaChart, Area, LineChart, Line,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'
import TrackRecordBadge from './TrackRecordBadge'

const API = '/api'

const FLAG_COLOR = { WATCH: '#fb923c', SIGNAL: '#f87171' }

function ToggleBtn({ id, label, view, setView }) {
  return (
    <button
      type="button"
      onClick={() => setView(id)}
      className={`font-mono text-[9px] tracking-wider px-2 py-0.5 rounded border transition-colors ${
        view === id ? 'text-cyan-glow border-cyan-glow/50 bg-cyan-glow/5' : 'text-neutral-600 border-border hover:text-neutral-400'
      }`}
    >
      {label}
    </button>
  )
}

// Custom dot: render a colored marker only on flagged days; return null
// otherwise so recharts emits no element for the ~110 unflagged points.
function FlagDot({ cx, cy, payload }) {
  const color = payload?.flag ? FLAG_COLOR[payload.flag] : null
  if (!color || cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={3} fill={color} stroke="none" />
}

export default function GasBalancePanel() {
  const { range } = useViewState()
  const { data, loading, error } = useFetchWithError(`${API}/gas/balance?days=${rangeDays(range)}`, { deps: [range] })
  const [view, setView] = useState('residual') // 'residual' | 'decomp'

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EU GAS BALANCE // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest
  const flaggedCount = rows.filter((r) => r.flag).length
  const flag = latest?.flag
  const flagColor = flag ? FLAG_COLOR[flag] : '#737373'

  return (
    <Panel
      id="gas-balance"
      title="EU GAS BALANCE · RESIDUAL"
      info="The residual = implied ΔStorage (supply − demand − exports) vs actual ΔStorage (AGSI), 7d-smoothed and z-scored. Persistent deviation = demand destruction or unexpected flows the market hasn't priced. Descriptive, not predictive."
      collapsible
      headerRight={
        latest && (
          <span className="font-mono text-[10px] font-bold" style={{ color: flagColor }}>
            {flag || 'OK'}
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Computing balance…</div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3 flex-wrap">
              <span className="font-mono text-3xl font-bold" style={{ color: flagColor }}>
                {latest.residual_7d == null ? '—' : `${latest.residual_7d >= 0 ? '+' : ''}${Math.round(latest.residual_7d).toLocaleString()}`}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">GWh/7d residual</span>
              <span className="font-mono text-[10px] text-yellow-400 border border-yellow-400/30 rounded px-1.5 py-0.5">
                z {latest.z_score?.toFixed(2)}
              </span>
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-1">
              {flaggedCount} flagged days / 120 · supply {latest.supply_gwh == null ? '—' : Math.round(latest.supply_gwh).toLocaleString()} − demand {latest.demand_gwh == null ? '—' : Math.round(latest.demand_gwh).toLocaleString()} GWh
            </div>
          </div>

          <div className="flex items-center gap-1.5 px-4 py-2 border-b border-border/30">
            <ToggleBtn id="residual" label="RESIDUAL" view={view} setView={setView} />
            <ToggleBtn id="decomp" label="IMPLIED vs ACTUAL" view={view} setView={setView} />
          </div>

          <div className="px-2 py-2">
            {view === 'residual' && rows.length > 1 && (
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={34} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v) => [`${Math.round(v).toLocaleString()} GWh`, 'residual']} labelFormatter={fmtDate} />
                  <ReferenceLine y={0} stroke="#444" />
                  <Area type="monotone" dataKey="residual" stroke="#22d3ee" fill="#22d3ee" fillOpacity={0.05} strokeWidth={1.5} dot={<FlagDot />} activeDot={{ r: 3 }} />
                </AreaChart>
              </ResponsiveContainer>
            )}
            {view === 'decomp' && rows.length > 1 && (
              <ResponsiveContainer width="100%" height={140}>
                <LineChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }} tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={60} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={34} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v, name) => [`${Math.round(v).toLocaleString()} GWh`, name]} labelFormatter={fmtDate} />
                  <Line type="monotone" dataKey="implied_delta" name="implied ΔStorage" stroke="#22d3ee" strokeWidth={1.5} dot={false} />
                  <Line type="monotone" dataKey="actual_delta" name="actual ΔStorage" stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
            <div className="flex items-center justify-center gap-4 mt-1 font-mono text-[8px] text-neutral-600">
              {view === 'residual' ? (
                <>
                  <span className="text-cyan-glow">▬ residual</span>
                  <span style={{ color: FLAG_COLOR.WATCH }}>● WATCH</span>
                  <span style={{ color: FLAG_COLOR.SIGNAL }}>● SIGNAL</span>
                </>
              ) : (
                <>
                  <span className="text-cyan-glow">▬ implied (supply−demand)</span>
                  <span className="text-neutral-400">▬ actual (AGSI)</span>
                </>
              )}
            </div>
          </div>
        </>
      )}
      <TrackRecordBadge signal="gas_residual" targetLabel="TTF" />
    </Panel>
  )
}
