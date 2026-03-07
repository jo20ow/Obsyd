import { useState, useEffect, useCallback } from 'react'
import { InfoPopover } from './Panel'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts'

const API = '/api'

// Sorted by crude oil relevance (Panama last — LNG/products only, no crude)
const CP_ORDER = ['hormuz', 'malacca', 'suez', 'cape', 'panama']
const CP_SHORT = {
  'Strait of Hormuz': 'hormuz',
  'Suez Canal': 'suez',
  'Malacca Strait': 'malacca',
  'Panama Canal': 'panama',
  'Cape of Good Hope': 'cape',
}

function anomalyColor(pct) {
  if (pct > 20) return 'text-green-glow'
  if (pct < -20) return 'text-red-400'
  return 'text-neutral-400'
}

function anomalyBorder(pct) {
  if (pct > 20) return 'border-green-glow/30'
  if (pct < -20) return 'border-red-500/30'
  return 'border-border'
}

function anomalyBg(pct) {
  if (pct > 20) return 'bg-green-glow/5'
  if (pct < -20) return 'bg-red-500/5'
  return 'bg-surface-light'
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00Z')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatDateShort(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00Z')
  return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="border border-border bg-surface px-3 py-2 font-mono text-[10px]">
      <div className="text-neutral-500 mb-1">{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ color: p.color }}>
          {p.name}: {p.value}{p.dataKey === 'brent' ? ' $/bbl' : ''}
        </div>
      ))}
    </div>
  )
}

function DisruptionBanner({ disruptions }) {
  if (!disruptions || disruptions.length === 0) return null

  return (
    <div className="border border-red-500/30 bg-red-500/8 rounded px-4 py-2.5 mb-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
        <span className="font-mono text-[10px] text-red-400 tracking-wider">
          ACTIVE DISRUPTIONS
        </span>
      </div>
      <div className="space-y-1">
        {disruptions.map((d) => (
          <div key={d.event_id} className="flex items-center gap-2">
            <span className="font-mono text-[10px] text-red-300 font-semibold">
              {d.event_name}
            </span>
            {d.country && (
              <span className="font-mono text-[9px] text-neutral-500">
                {d.country}
              </span>
            )}
            {d.affected_portname && (
              <span className="font-mono text-[9px] text-neutral-600">
                // {d.affected_portname}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ChokePointCard({ cp, selected, onClick }) {
  const anom = cp.anomaly_total_pct
  const isSelected = selected === (CP_SHORT[cp.name] || cp.portid)
  const isPanama = (CP_SHORT[cp.name] || cp.portid) === 'panama'

  return (
    <button
      onClick={onClick}
      className={`w-full text-left border rounded px-3 py-2.5 transition-all cursor-pointer
        ${isPanama ? 'border-border bg-surface-light opacity-60' : `${anomalyBorder(anom)} ${anomalyBg(anom)}`}
        ${isSelected ? 'ring-1 ring-cyan-glow/40' : 'hover:border-neutral-600'}
      `}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] text-neutral-400 tracking-wider">
            {cp.name.toUpperCase()}
          </span>
          {isPanama && (
            <span className="font-mono text-[8px] text-neutral-600 border border-neutral-700 rounded px-1">
              LNG/PRODUCTS
            </span>
          )}
        </div>
        <span className="font-mono text-[9px] text-neutral-600">
          {formatDate(cp.date)}
        </span>
      </div>

      <div className="flex items-end justify-between">
        <div>
          <span className="font-mono text-lg font-bold text-cyan-glow">
            {cp.n_total}
          </span>
          <span className="font-mono text-[10px] text-neutral-500 ml-1">vessels</span>
          <span className="font-mono text-xs text-neutral-600 ml-2">
            ({cp.n_tanker} tanker)
          </span>
        </div>

        <div className="text-right">
          <div className={`font-mono text-sm font-bold ${anomalyColor(anom)}`}>
            {anom > 0 ? '+' : ''}{anom.toFixed(1)}%
          </div>
          <div className="font-mono text-[9px] text-neutral-600">
            avg30: {cp.avg_total_30d?.toFixed(0)}
          </div>
        </div>
      </div>
    </button>
  )
}

const HISTORY_TIMEFRAMES = [
  { label: '30D', days: 30 },
  { label: '90D', days: 90 },
  { label: '180D', days: 180 },
  { label: '1Y', days: 365 },
  { label: 'ALL', days: 2600 },
]

function HistoryChart({ name, history, oilPrices, timeframe, onTimeframeChange }) {
  if (!history || history.length === 0) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3 h-64 flex items-center justify-center">
        <span className="font-mono text-[10px] text-neutral-600">
          SELECT A CHOKEPOINT TO VIEW HISTORY
        </span>
      </div>
    )
  }

  // Merge chokepoint data with Brent prices by date
  const brentMap = {}
  if (oilPrices?.DCOILBRENTEU?.data) {
    for (const p of oilPrices.DCOILBRENTEU.data) {
      brentMap[p.date] = p.value
    }
  }

  // Only PortWatch transit data — AIS geofence data shown separately
  const pwHistory = history.filter((d) => d.source !== 'ais')

  const chartData = pwHistory.map((d) => ({
    date: d.date,
    n_total: d.n_total,
    n_tanker: d.n_tanker,
    brent: brentMap[d.date] ?? null,
  }))

  const hasBrent = chartData.some((d) => d.brent !== null)

  return (
    <div className="border border-border bg-surface rounded px-4 py-3">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="font-mono text-[10px] text-neutral-600 tracking-wider">
          {name?.toUpperCase()} // TRANSIT HISTORY
          {hasBrent && <span className="text-orange-400/60 ml-2">+ BRENT</span>}
        </div>
        <div className="flex items-center gap-1">
          {HISTORY_TIMEFRAMES.map((tf) => (
            <button
              key={tf.label}
              onClick={() => onTimeframeChange(tf)}
              className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${
                timeframe.label === tf.label
                  ? 'bg-cyan-glow/20 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {tf.label}
            </button>
          ))}
          <span className="font-mono text-[9px] text-neutral-700 ml-1">
            {pwHistory.length}d
          </span>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData} margin={{ top: 5, right: hasBrent ? 45 : 5, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 9, fill: '#555', fontFamily: 'monospace' }}
            tickFormatter={formatDateShort}
            interval="preserveStartEnd"
            minTickGap={60}
          />
          <YAxis
            yAxisId="left"
            tick={{ fontSize: 9, fill: '#00e5ff88', fontFamily: 'monospace' }}
            width={35}
          />
          {hasBrent && (
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 9, fill: '#ff884488', fontFamily: 'monospace' }}
              width={40}
              tickFormatter={(v) => `$${v}`}
            />
          )}
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 9, fontFamily: 'monospace' }}
            iconSize={8}
          />
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="n_total"
            name="Total Vessels"
            stroke="#00e5ff"
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 3 }}
            connectNulls={false}
          />
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="n_tanker"
            name="Tanker"
            stroke="#00ff9d"
            strokeWidth={1}
            strokeDasharray="4 3"
            dot={false}
            activeDot={{ r: 3 }}
            connectNulls={false}
          />
          {hasBrent && (
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="brent"
              name="Brent"
              stroke="#ff8844"
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
              connectNulls
            />
          )}
        </LineChart>
      </ResponsiveContainer>
      {(() => {
        const lastPwDate = pwHistory.at(-1)?.date
        if (!lastPwDate) return null
        const ageMs = Date.now() - new Date(lastPwDate + 'T00:00:00Z').getTime()
        const ageDays = ageMs / (1000 * 60 * 60 * 24)
        if (ageDays <= 2) return null
        return (
          <div className="font-mono text-[9px] text-neutral-600 mt-2">
            PortWatch bis {formatDate(lastPwDate)} — IMF aktualisiert mit 3-5 Tagen Verzögerung
          </div>
        )
      })()}
    </div>
  )
}

const DEFAULT_TF = HISTORY_TIMEFRAMES[2] // 180D

export default function ChokePointMonitor() {
  const [summary, setSummary] = useState(null)
  const [selected, setSelected] = useState('hormuz')
  const [history, setHistory] = useState(null)
  const [historyName, setHistoryName] = useState('')
  const [historyTf, setHistoryTf] = useState(DEFAULT_TF)
  const [oilPrices, setOilPrices] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch(`${API}/portwatch/summary`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API}/prices/oil?days=365`).then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([summaryData, oilData]) => {
        setSummary(summaryData)
        if (oilData?.series) setOilPrices(oilData.series)
        setLoading(false)
      })
      .catch((e) => { console.error('ChokePointMonitor fetch:', e); setLoading(false) })
  }, [])

  const fetchHistory = useCallback((name, days) => {
    setSelected(name)
    setHistoryName(name)
    fetch(`${API}/portwatch/chokepoints/${name}/history?days=${days}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setHistory(data.history)
      })
      .catch((e) => console.error('ChokePointMonitor history:', e))
  }, [])

  useEffect(() => {
    fetchHistory('hormuz', historyTf.days)
  }, [fetchHistory, historyTf])

  if (loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-8">
        <div className="font-mono text-[10px] text-neutral-600 animate-pulse text-center">
          PORTWATCH // LOADING ...
        </div>
      </div>
    )
  }

  if (!summary) return null

  const chokepoints = summary.chokepoints || []
  const disruptions = summary.disruptions || []

  const sorted = [...chokepoints].sort((a, b) => {
    const ai = CP_ORDER.indexOf(CP_SHORT[a.name] || '')
    const bi = CP_ORDER.indexOf(CP_SHORT[b.name] || '')
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })

  return (
    <div id="chokepoint-monitor">
      <DisruptionBanner disruptions={disruptions} />

      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="flex items-center gap-2 mb-3">
          <span className="font-mono text-[10px] text-neutral-600 tracking-wider">CHOKEPOINT MONITOR // IMF PORTWATCH</span>
          <InfoPopover text="Vessel traffic at 5 global chokepoints. Source: IMF PortWatch, 3-5 day publication delay. Anomaly = deviation from 30-day average." />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2 mb-4">
          {sorted.map((cp) => {
            const slug = CP_SHORT[cp.name] || cp.portid
            return (
              <ChokePointCard
                key={cp.portid}
                cp={cp}
                selected={selected}
                onClick={() => fetchHistory(slug, historyTf.days)}
              />
            )
          })}
        </div>

        <HistoryChart
          name={historyName}
          history={history}
          oilPrices={oilPrices}
          timeframe={historyTf}
          onTimeframeChange={(tf) => {
            setHistoryTf(tf)
            if (selected) fetchHistory(selected, tf.days)
          }}
        />

        <div className="mt-2 font-mono text-[8px] text-neutral-700">
          Source: IMF PortWatch (portwatch.imf.org) // Anomaly = current vs 30d avg
        </div>
      </div>
    </div>
  )
}
