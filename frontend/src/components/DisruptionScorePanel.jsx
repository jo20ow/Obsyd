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
} from 'recharts'

const API = '/api'

const SEVERITY = [
  { max: 25, label: 'LOW — Supply chains operating normally', color: 'text-green-glow', bg: 'bg-green-glow', border: 'border-green-glow/20' },
  { max: 50, label: 'MODERATE — Some stress signals present', color: 'text-yellow-400', bg: 'bg-yellow-400', border: 'border-yellow-400/20' },
  { max: 75, label: 'HIGH — Significant disruption indicators', color: 'text-orange-400', bg: 'bg-orange-400', border: 'border-orange-400/20' },
  { max: 101, label: 'CRITICAL — Multiple disruption signals converging', color: 'text-red-400', bg: 'bg-red-400', border: 'border-red-400/20' },
]

function getSeverity(score) {
  return SEVERITY.find((s) => score <= s.max) || SEVERITY[3]
}

function BreakdownBar({ label, score, weight }) {
  const sev = getSeverity(score)
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[9px] text-neutral-500 w-[100px] truncate">{label}</span>
      <div className="flex-1 h-1.5 bg-neutral-800 rounded-full">
        <div
          className={`h-1.5 rounded-full ${sev.bg} transition-all`}
          style={{ width: `${Math.min(100, score)}%`, opacity: 0.7 }}
        />
      </div>
      <span className={`font-mono text-[9px] w-8 text-right ${sev.color}`}>
        {score.toFixed(0)}
      </span>
      <span className="font-mono text-[8px] text-neutral-700 w-6 text-right">{weight}%</span>
    </div>
  )
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="border border-border bg-surface px-3 py-2 font-mono text-[10px]">
      <div className="text-neutral-500 mb-1">{label}</div>
      <div className={getSeverity(payload[0]?.value || 0).color}>
        Score: {payload[0]?.value?.toFixed(1)}
      </div>
    </div>
  )
}

export default function DisruptionScorePanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/analytics/disruption-score?days=90`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setData(d)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  if (!data?.available && !loading) return null

  const score = data?.current?.score ?? 0
  const sev = getSeverity(score)
  const breakdown = data?.breakdown

  return (
    <Panel
      id="disruption-score"
      title="SUPPLY DISRUPTION INDEX"
      info="Composite score (0-100) combining 6 supply chain stress indicators: Hormuz transits, Cape rerouting, floating storage, crack spreads, backwardation, and geopolitical sentiment."
      collapsible
      headerRight={
        data?.available && (
          <span className={`font-mono text-[10px] font-bold ${sev.color}`}>
            {score.toFixed(0)}/100
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Computing disruption score...
        </div>
      )}
      {!loading && data?.available && (
        <>
          {/* Score header */}
          <div className={`px-4 py-3 border-b ${sev.border}`}>
            <div className="flex items-center gap-3">
              <span className={`font-mono text-3xl font-bold ${sev.color}`}>
                {score.toFixed(0)}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">/ 100</span>
            </div>
            <div className={`font-mono text-[10px] mt-1 ${sev.color}`}>
              {sev.label}
            </div>
          </div>

          {/* Breakdown bars */}
          {breakdown && (
            <div className="px-4 py-3 space-y-2 border-b border-border/30">
              {Object.entries(breakdown).map(([key, comp]) => (
                <BreakdownBar
                  key={key}
                  label={comp.label}
                  score={comp.score}
                  weight={comp.weight}
                />
              ))}
            </div>
          )}

          {/* Sparkline */}
          {data.history?.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={80}>
                <AreaChart data={data.history} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    tickFormatter={(d) => {
                      const dt = new Date(d + 'T00:00:00Z')
                      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                    }}
                    interval="preserveStartEnd"
                    minTickGap={60}
                  />
                  <YAxis
                    tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }}
                    width={20}
                    domain={[0, 100]}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="score"
                    stroke={sev.color.includes('green') ? '#00ff9d' : sev.color.includes('yellow') ? '#facc15' : sev.color.includes('orange') ? '#fb923c' : '#f87171'}
                    fill={sev.color.includes('green') ? '#00ff9d' : sev.color.includes('yellow') ? '#facc15' : sev.color.includes('orange') ? '#fb923c' : '#f87171'}
                    fillOpacity={0.06}
                    strokeWidth={1.5}
                    dot={false}
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
