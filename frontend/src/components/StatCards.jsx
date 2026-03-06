import { useState, useEffect } from 'react'

const ENERGY_CARDS = [
  {
    id: 'PET.RWTC.W',
    liveKey: 'WTI',
    label: 'WTI CRUDE',
    unit: '$/bbl',
    valueClass: 'text-cyan-glow',
  },
  {
    id: 'PET.RBRTE.W',
    liveKey: 'BRENT',
    label: 'BRENT CRUDE',
    unit: '$/bbl',
    valueClass: 'text-green-glow',
  },
  {
    id: 'NG.RNGWHHD.W',
    liveKey: 'NG',
    label: 'HENRY HUB NG',
    unit: '$/MMBtu',
    valueClass: 'text-cyan-glow',
  },
  {
    id: 'PET.WCSSTUS1.W',
    liveKey: null,
    label: 'CUSHING STOCKS',
    unit: 'k bbl',
    valueClass: 'text-green-glow',
  },
]

const COMMODITY_CARDS = [
  { key: 'GOLD', label: 'GOLD', unit: '$/oz', valueClass: 'text-yellow-400' },
  { key: 'SILVER', label: 'SILVER', unit: '$/oz', valueClass: 'text-neutral-300' },
  { key: 'COPPER', label: 'COPPER', unit: '$/lb', valueClass: 'text-orange-400' },
]

function getLatestTwo(data, seriesId) {
  const filtered = data
    .filter((r) => r.series_id === seriesId && r.value != null)
    .sort((a, b) => (a.period > b.period ? -1 : 1))
  return [filtered[0], filtered[1]]
}

function formatValue(value, seriesId) {
  if (value == null) return '--'
  if (seriesId === 'PET.WCSSTUS1.W') return value.toLocaleString('en-US', { maximumFractionDigits: 0 })
  return value.toFixed(2)
}

function SourceBadge({ isLive, liveSource }) {
  if (!isLive) return <span className="font-mono text-[10px] text-neutral-600">WEEKLY</span>

  const badges = {
    twelvedata: { dot: 'bg-purple-400 shadow-[0_0_4px_#a855f7]', text: 'text-purple-400', label: 'LIVE' },
    alphavantage: { dot: 'bg-green-glow shadow-[0_0_4px_var(--color-green-glow)]', text: 'text-green-glow', label: 'LIVE' },
    fred: { dot: 'bg-cyan-glow/60', text: 'text-cyan-glow/80', label: 'DAILY' },
  }
  const b = badges[liveSource] || badges.alphavantage

  return (
    <span className="flex items-center gap-1">
      <span className={`w-1.5 h-1.5 rounded-full ${b.dot} animate-pulse`} />
      <span className={`font-mono text-[10px] ${b.text}`}>{b.label}</span>
    </span>
  )
}

function PriceCard({ label, value, unit, changePct, changeLabel, date, isLive, liveSource, valueClass }) {
  return (
    <div className="border border-border bg-surface rounded px-4 py-3">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-neutral-500">{label}</span>
          <SourceBadge isLive={isLive} liveSource={liveSource} />
        </div>
        <span className="font-mono text-[10px] text-neutral-600">{date || ''}</span>
      </div>
      <div className="flex items-end justify-between">
        <div>
          <span className={`font-mono text-2xl font-bold ${valueClass}`}>
            {value != null ? value.toFixed(2) : '--'}
          </span>
          <span className="font-mono text-xs text-neutral-500 ml-2">{unit}</span>
        </div>
        {changePct != null && (
          <div className="text-right">
            <div className={`font-mono text-sm font-semibold ${changePct >= 0 ? 'text-green-glow' : 'text-red-400'}`}>
              {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
            </div>
            <div className="font-mono text-[10px] text-neutral-600">{changeLabel}</div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function StatCards({ data, live, liveSource }) {
  const [commodities, setCommodities] = useState(null)

  useEffect(() => {
    fetch('/api/prices/commodities')
      .then((r) => (r.ok ? r.json() : null))
      .then(setCommodities)
      .catch((e) => console.error('Commodities fetch:', e))
  }, [])

  const metals = commodities?.metals || {}
  const hasMetals = Object.keys(metals).length > 0

  return (
    <div className="space-y-3">
      {/* Row 1: Energy */}
      <div className="grid grid-cols-1 gap-3">
        {ENERGY_CARDS.map((cfg) => {
          const liveQuote = cfg.liveKey && live?.[cfg.liveKey]
          const isLive = !!liveQuote

          let value, changePct, changeLabel, date
          if (isLive) {
            value = liveQuote.current
            changePct = liveQuote.change_pct
            changeLabel = 'vs prev day'
            date = liveQuote.date
          } else {
            const [latest, prev] = getLatestTwo(data, cfg.id)
            value = latest?.value ?? null
            date = latest?.period || ''
            if (latest?.value != null && prev?.value != null) {
              changePct = ((latest.value - prev.value) / prev.value) * 100
            } else {
              changePct = null
            }
            changeLabel = 'vs prev week'
          }

          // Cushing uses its own formatting
          const displayValue = cfg.id === 'PET.WCSSTUS1.W' ? null : value

          return (
            <div key={cfg.id} className="border border-border bg-surface rounded px-4 py-3">
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-neutral-500">{cfg.label}</span>
                  <SourceBadge isLive={isLive} liveSource={liveSource} />
                </div>
                <span className="font-mono text-[10px] text-neutral-600">{date}</span>
              </div>
              <div className="flex items-end justify-between">
                <div>
                  <span className={`font-mono text-2xl font-bold ${cfg.valueClass}`}>
                    {formatValue(value, cfg.id)}
                  </span>
                  <span className="font-mono text-xs text-neutral-500 ml-2">{cfg.unit}</span>
                </div>
                {changePct != null && (
                  <div className="text-right">
                    <div className={`font-mono text-sm font-semibold ${changePct >= 0 ? 'text-green-glow' : 'text-red-400'}`}>
                      {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                    </div>
                    <div className="font-mono text-[10px] text-neutral-600">{changeLabel}</div>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Row 2: Metals (only if data available) */}
      {hasMetals && (
        <div className="grid grid-cols-3 gap-3">
          {COMMODITY_CARDS.map((cfg) => {
            const q = metals[cfg.key]
            if (!q) return null
            return (
              <PriceCard
                key={cfg.key}
                label={cfg.label}
                value={q.current}
                unit={cfg.unit}
                changePct={q.change_pct}
                changeLabel="vs prev day"
                date={q.date}
                isLive={true}
                liveSource={commodities?.source}
                valueClass={cfg.valueClass}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
