import { useState, useEffect } from 'react'
import Panel from './Panel'
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

function interpretIndex(val) {
  if (val > 110) return { text: 'Elevated — rerouting is binding fleet capacity', color: 'text-orange-400' }
  if (val < 90) return { text: 'Low — excess fleet capacity available', color: 'text-green-glow' }
  return { text: 'Normal', color: 'text-neutral-400' }
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  return (
    <div className="border border-border bg-surface px-3 py-2 font-mono text-[10px]">
      <div className="text-neutral-500 mb-1">{label}</div>
      <div className="text-cyan-glow">Index: {d?.index?.toFixed(1)}</div>
      <div className="text-neutral-400">Cape share: {(d?.cape_share * 100)?.toFixed(1)}%</div>
      <div className="text-neutral-500">Avg distance: {d?.avg_distance?.toLocaleString()} nm</div>
    </div>
  )
}

export default function TonneMilesPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(90)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/analytics/tonne-miles?days=${days}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setData(d)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [days])

  if (!data?.available && !loading) return null

  const current = data?.current
  const interp = current ? interpretIndex(current.index) : null

  return (
    <Panel
      id="tonne-miles"
      title="TONNE-MILES INDEX"
      info="Measures how much transport capacity is consumed by current routing patterns. Higher = more tankers tied up on longer routes = tighter freight market. Baseline (30d avg) = 100."
      collapsible
      headerRight={
        current && (
          <span className={`font-mono text-[10px] font-bold ${interp.color}`}>
            {current.index?.toFixed(0)}
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Computing tonne-miles...
        </div>
      )}
      {!loading && current && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-center gap-4">
              <div>
                <span className="font-mono text-2xl font-bold text-cyan-glow">
                  {current.index?.toFixed(0)}
                </span>
                <span className="font-mono text-[10px] text-neutral-600 ml-2">/ 100</span>
              </div>
              <div className="flex flex-col">
                {data.change_7d != null && (
                  <span className={`font-mono text-[10px] ${data.change_7d >= 0 ? 'text-orange-400' : 'text-green-glow'}`}>
                    {data.change_7d >= 0 ? '+' : ''}{data.change_7d} vs 7d
                  </span>
                )}
                {data.change_30d != null && (
                  <span className={`font-mono text-[10px] ${data.change_30d >= 0 ? 'text-orange-400' : 'text-green-glow'}`}>
                    {data.change_30d >= 0 ? '+' : ''}{data.change_30d} vs 30d
                  </span>
                )}
              </div>
            </div>
            <div className={`font-mono text-[10px] mt-1 ${interp.color}`}>
              {interp.text}
            </div>
            <div className="font-mono text-[9px] text-neutral-600 mt-1">
              Cape share: {(current.cape_share * 100).toFixed(1)}% · Avg distance: {current.avg_distance?.toLocaleString()} nm
            </div>
          </div>

          <div className="px-4 py-2 border-b border-border/30 flex items-center justify-end gap-1">
            {[30, 90, 180].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${
                  days === d
                    ? 'bg-cyan-glow/20 text-cyan-glow'
                    : 'text-neutral-600 hover:text-neutral-400'
                }`}
              >
                {d}D
              </button>
            ))}
          </div>

          {data.history?.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={data.history} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 9, fill: '#555', fontFamily: 'monospace' }}
                    tickFormatter={(d) => {
                      const dt = new Date(d + 'T00:00:00Z')
                      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                    }}
                    interval="preserveStartEnd"
                    minTickGap={60}
                  />
                  <YAxis
                    tick={{ fontSize: 9, fill: '#00e5ff88', fontFamily: 'monospace' }}
                    width={30}
                    domain={['auto', 'auto']}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <ReferenceLine y={100} stroke="#555" strokeDasharray="4 3" />
                  <ReferenceLine y={110} stroke="#f59e0b44" strokeDasharray="4 3" />
                  <Area
                    type="monotone"
                    dataKey="index"
                    stroke="#00e5ff"
                    fill="#00e5ff"
                    fillOpacity={0.08}
                    strokeWidth={1.5}
                    dot={false}
                    activeDot={{ r: 3 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
