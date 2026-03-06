import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts'

const API = '/api'

const STATE_STYLES = {
  normal: { text: 'text-green-glow', label: 'NORMAL', bg: 'bg-green-glow/5', border: 'border-green-glow/20' },
  elevated: { text: 'text-yellow-400', label: 'ELEVATED', bg: 'bg-yellow-500/5', border: 'border-yellow-500/20' },
  high_rerouting: { text: 'text-red-400', label: 'HIGH REROUTING', bg: 'bg-red-500/5', border: 'border-red-500/20' },
}

function formatDateShort(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00Z')
  return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  return (
    <div className="border border-border bg-surface px-3 py-2 font-mono text-[10px]">
      <div className="text-neutral-500 mb-1">{label}</div>
      <div className="text-cyan-glow">Cape share: {(d.ratio * 100).toFixed(1)}%</div>
      <div className="text-neutral-400">Suez: {d.suez_tanker} tankers</div>
      <div className="text-neutral-400">Cape: {d.cape_tanker} tankers</div>
    </div>
  )
}

export default function ReroutingIndex() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API}/signals/rerouting-index?days=365`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch((e) => console.error('ReroutingIndex fetch:', e))
  }, [])

  if (!data?.available) return null

  const { current, history, events } = data
  const style = STATE_STYLES[current.state] || STATE_STYLES.normal

  // Downsample history for chart (every 3rd point to keep it light)
  const chartData = history.filter((_, i) => i % 3 === 0 || i === history.length - 1)
    .map((d) => ({ ...d, ratio_pct: +(d.ratio * 100).toFixed(1) }))

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div className="font-mono text-[10px] text-neutral-600 tracking-wider">
          CAPE / SUEZ REROUTING INDEX
        </div>
        <div className={`font-mono text-[10px] font-bold ${style.text}`}>
          {style.label}
        </div>
      </div>

      {/* Current status */}
      <div className={`px-4 py-3 border-b border-border/50 ${style.bg}`}>
        <div className="flex items-center justify-between">
          <div>
            <span className={`font-mono text-2xl font-bold ${style.text}`}>
              {current.ratio_pct}%
            </span>
            <span className="font-mono text-[10px] text-neutral-500 ml-2">
              Cape share (7d avg)
            </span>
          </div>
          <div className="text-right font-mono text-[10px]">
            <div className="text-neutral-500">
              Suez: <span className="text-neutral-300">{current.suez_tanker_7d_avg}</span> tankers/d
            </div>
            <div className="text-neutral-500">
              Cape: <span className="text-neutral-300">{current.cape_tanker_7d_avg}</span> tankers/d
            </div>
          </div>
        </div>
        <div className="font-mono text-[9px] text-neutral-600 mt-1">
          30d baseline: {(current.baseline_30d * 100).toFixed(1)}% // 365d avg: {(current.baseline_365d * 100).toFixed(1)}%
          {current.anomaly_pct !== 0 && (
            <span className={current.anomaly_pct > 20 ? 'text-red-400 ml-1' : current.anomaly_pct < -20 ? 'text-green-glow ml-1' : 'text-neutral-500 ml-1'}>
              ({current.anomaly_pct > 0 ? '+' : ''}{current.anomaly_pct.toFixed(0)}% vs avg)
            </span>
          )}
        </div>
      </div>

      {/* Chart */}
      <div className="px-4 py-3">
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 9, fill: '#555', fontFamily: 'monospace' }}
              tickFormatter={formatDateShort}
              interval="preserveStartEnd"
              minTickGap={60}
            />
            <YAxis
              tick={{ fontSize: 9, fill: '#00e5ff88', fontFamily: 'monospace' }}
              width={30}
              tickFormatter={(v) => `${v}%`}
              domain={[0, 'auto']}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={30} stroke="#eab30855" strokeDasharray="5 5" label={{ value: '30%', fontSize: 8, fill: '#eab30855' }} />
            <ReferenceLine y={40} stroke="#ef444455" strokeDasharray="5 5" label={{ value: '40%', fontSize: 8, fill: '#ef444455' }} />
            <Area
              type="monotone"
              dataKey="ratio_pct"
              name="Cape share"
              stroke="#00e5ff"
              fill="#00e5ff"
              fillOpacity={0.1}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Historical rerouting events */}
      {events && events.length > 0 && (
        <div className="px-4 py-2 border-t border-border/50">
          <div className="font-mono text-[9px] text-neutral-600 mb-1">REROUTING EVENTS (Cape &gt;35% sustained)</div>
          {events.slice(-3).map((ev) => (
            <div key={ev.start_date} className="flex items-center gap-2 py-0.5">
              <span className="font-mono text-[9px] text-neutral-500">{ev.start_date}</span>
              <span className="font-mono text-[9px] text-neutral-600">→</span>
              <span className="font-mono text-[9px] text-neutral-500">{ev.end_date}</span>
              <span className="font-mono text-[9px] text-neutral-400">({ev.duration_days}d)</span>
              <span className="font-mono text-[9px] text-red-400">peak {(ev.peak_ratio * 100).toFixed(0)}%</span>
              {ev.ongoing && <span className="font-mono text-[8px] text-red-400 animate-pulse">ACTIVE</span>}
            </div>
          ))}
        </div>
      )}

      <div className="px-4 py-1.5 border-t border-border/50 font-mono text-[8px] text-neutral-700">
        Cape share of combined Suez+Cape tanker traffic // Normal ~20%, disruption &gt;35%
      </div>
    </div>
  )
}
