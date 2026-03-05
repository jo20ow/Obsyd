import { useState, useEffect } from 'react'

const API = '/api'

const TOP5 = ['SA', 'RU', 'US', 'IQ', 'CA']

const FLAG = { SA: '🇸🇦', RU: '🇷🇺', US: '🇺🇸', IQ: '🇮🇶', CA: '🇨🇦' }
const SHORT = { SA: 'KSA', RU: 'RUS', US: 'USA', IQ: 'IRQ', CA: 'CAN' }

export default function JODIPanel() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API}/jodi/summary`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {})
  }, [])

  if (!data || data.length === 0) return null

  const top5 = data
    .filter((r) => TOP5.includes(r.country) && r.production != null)
    .sort((a, b) => (b.production || 0) - (a.production || 0))

  if (top5.length === 0) return null

  const maxProd = Math.max(...top5.map((r) => r.production || 0), 1)
  const latestDate = top5[0]?.date || ''

  return (
    <div className="border border-border bg-surface rounded px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <div className="font-mono text-[10px] text-neutral-600 tracking-wider">
          GLOBAL OIL PRODUCTION // JODI
        </div>
        {latestDate && (
          <span className="font-mono text-[9px] text-neutral-600">{latestDate}</span>
        )}
      </div>

      <div className="space-y-2">
        {top5.map((r) => {
          const pct = (r.production / maxProd) * 100
          const kbd = r.production / 30.44 // KBBL/month → approx KBD
          return (
            <div key={r.country}>
              <div className="flex items-center justify-between mb-0.5">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs">{FLAG[r.country] || ''}</span>
                  <span className="font-mono text-[10px] text-neutral-400">
                    {SHORT[r.country] || r.country}
                  </span>
                </div>
                <span className="font-mono text-[10px] text-cyan-glow font-semibold">
                  {(kbd / 1000).toFixed(1)}
                  <span className="text-neutral-500 ml-0.5">Mbd</span>
                </span>
              </div>
              <div className="w-full h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                <div
                  className="h-1.5 rounded-full bg-cyan-glow/60 transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-2 font-mono text-[8px] text-neutral-700">
        Source: JODI Oil World Database (KBBL/month → Mbd estimate)
      </div>
    </div>
  )
}
