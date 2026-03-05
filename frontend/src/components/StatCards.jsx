const SERIES_CONFIG = [
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

export default function StatCards({ data, live }) {
  return (
    <div className="grid grid-cols-1 gap-3">
      {SERIES_CONFIG.map((cfg) => {
        const liveQuote = cfg.liveKey && live?.[cfg.liveKey]
        const isLive = !!liveQuote

        let value, changePct, changeLabel
        if (isLive) {
          value = liveQuote.current
          changePct = liveQuote.change_pct
          changeLabel = 'vs prev day'
        } else {
          const [latest, prev] = getLatestTwo(data, cfg.id)
          value = latest?.value ?? null
          if (latest?.value != null && prev?.value != null) {
            changePct = ((latest.value - prev.value) / prev.value) * 100
          } else {
            changePct = null
          }
          changeLabel = 'vs prev week'
        }

        const [latest] = getLatestTwo(data, cfg.id)

        return (
          <div
            key={cfg.id}
            className="border border-border bg-surface rounded px-4 py-3"
          >
            <div className="flex items-center justify-between mb-1.5">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-neutral-500">{cfg.label}</span>
                {isLive ? (
                  <span className="flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-glow shadow-[0_0_4px_var(--color-green-glow)] animate-pulse" />
                    <span className="font-mono text-[10px] text-green-glow">DAILY</span>
                  </span>
                ) : (
                  <span className="font-mono text-[10px] text-neutral-600">WEEKLY</span>
                )}
              </div>
              <span className="font-mono text-[10px] text-neutral-600">
                {isLive ? liveQuote.date : (latest?.period || '')}
              </span>
            </div>
            <div className="flex items-end justify-between">
              <div>
                <span className={`font-mono text-2xl font-bold ${cfg.valueClass}`}>
                  {formatValue(value, cfg.id)}
                </span>
                <span className="font-mono text-xs text-neutral-500 ml-2">
                  {cfg.unit}
                </span>
              </div>
              {changePct != null && (
                <div className="text-right">
                  <div
                    className={`font-mono text-sm font-semibold ${
                      changePct >= 0 ? 'text-green-glow' : 'text-red-400'
                    }`}
                  >
                    {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                  </div>
                  <div className="font-mono text-[10px] text-neutral-600">
                    {changeLabel}
                  </div>
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
