import { useState, useEffect } from 'react'
import { SkeletonCard } from './Skeleton'

const API = '/api'

const MACRO_SERIES = [
  { id: 'DTWEXBGS', label: 'DXY', unit: '', decimals: 2 },
  { id: 'DGS10', label: '10Y YIELD', unit: '%', decimals: 2 },
  { id: 'DGS2', label: '2Y YIELD', unit: '%', decimals: 2 },
  { id: 'FEDFUNDS', label: 'FED FUNDS', unit: '%', decimals: 2 },
]

export default function MacroPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API}/prices/fred?limit=200`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setData)
      .catch((e) => { console.error('MacroPanel fetch failed:', e); setError(e.message) })
  }, [])

  if (error) return (
    <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
      <div className="font-mono text-[10px] text-red-400">MACRO // FETCH ERROR</div>
    </div>
  )

  if (data === null) return <SkeletonCard lines={4} />
  if (data.length === 0) return null

  return (
    <div className="border border-border bg-surface rounded px-4 py-3">
      <div className="font-mono text-[10px] text-neutral-600 mb-2 tracking-wider">
        MACRO INDICATORS // FRED
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {MACRO_SERIES.map((cfg) => {
          const rows = data
            .filter((r) => r.series_id === cfg.id && r.value != null)
            .sort((a, b) => (a.date > b.date ? -1 : 1))

          const latest = rows[0]
          const prev = rows[1]

          if (!latest) return null

          const changePct =
            prev && prev.value !== 0
              ? ((latest.value - prev.value) / prev.value) * 100
              : null

          return (
            <div key={cfg.id} className="border border-border bg-surface-light rounded px-3 py-2">
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-[10px] text-neutral-500">{cfg.label}</span>
                <span className="font-mono text-[9px] text-neutral-600">{latest.date}</span>
              </div>
              <div className="flex items-end justify-between">
                <span className="font-mono text-lg font-bold text-cyan-glow">
                  {latest.value.toFixed(cfg.decimals)}
                  {cfg.unit && (
                    <span className="text-xs text-neutral-500 ml-1">{cfg.unit}</span>
                  )}
                </span>
                {changePct != null && (
                  <span
                    className={`font-mono text-[11px] font-semibold ${
                      changePct >= 0 ? 'text-green-glow' : 'text-red-400'
                    }`}
                  >
                    {changePct >= 0 ? '+' : ''}
                    {changePct.toFixed(2)}%
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
