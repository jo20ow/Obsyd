import { useState, useEffect } from 'react'

const TICKERS = [
  { key: 'WTI', label: 'WTI', color: 'text-cyan-glow' },
  { key: 'BRENT', label: 'BRENT', color: 'text-green-glow' },
  { key: 'NG', label: 'NG', color: 'text-purple-400' },
  { key: 'TTF', label: 'TTF', color: 'text-pink-400' },
  { key: 'GOLD', label: 'GOLD', color: 'text-yellow-400' },
  { key: 'COPPER', label: 'COPPER', color: 'text-orange-400' },
]

export default function PriceTicker() {
  const [prices, setPrices] = useState(null)
  const [source, setSource] = useState(null)

  useEffect(() => {
    let retryTimeout
    function fetchPrices() {
      fetch('/api/prices/live')
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (d?.available && d.prices) {
            setPrices(d.prices)
            setSource(d.source)
          } else {
            retryTimeout = setTimeout(fetchPrices, 10_000)
          }
        })
        .catch(() => {
          retryTimeout = setTimeout(fetchPrices, 10_000)
        })
    }
    fetchPrices()
    const interval = setInterval(fetchPrices, 2 * 60 * 1000)
    return () => { clearInterval(interval); clearTimeout(retryTimeout) }
  }, [])

  return (
    <div className="border border-border bg-surface rounded px-3 py-2 flex items-center flex-wrap gap-y-1.5 gap-x-1">
      <span className="font-mono text-[9px] text-neutral-600 tracking-wider shrink-0 mr-1">
        {source === 'yfinance' && (
          <span className="inline-flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-green-glow animate-pulse" />
            LIVE
          </span>
        )}
      </span>
      {TICKERS.map((t, i) => {
        const q = prices?.[t.key]
        const price = q?.current
        const pct = q?.change_pct
        return (
          <div key={t.key} className="flex items-center gap-1 shrink-0">
            {i > 0 && <span className="text-neutral-800 mx-1">|</span>}
            <span className="font-mono text-[10px] text-neutral-500">{t.label}</span>
            <span className={`font-mono text-xs font-bold ${t.color}`}>
              {price != null ? price.toFixed(2) : '--'}
            </span>
            {pct != null && (
              <span className={`font-mono text-[10px] ${pct >= 0 ? 'text-green-glow' : 'text-red-400'}`}>
                {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
