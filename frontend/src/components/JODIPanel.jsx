import { useState, useEffect, useMemo } from 'react'
import { SkeletonCard } from './Skeleton'
import Panel from './Panel'

const API = '/api'

const TOP5 = ['SA', 'RU', 'US', 'IQ', 'CA']

const FLAG = { SA: '🇸🇦', RU: '🇷🇺', US: '🇺🇸', IQ: '🇮🇶', CA: '🇨🇦' }
const SHORT = { SA: 'KSA', RU: 'RUS', US: 'USA', IQ: 'IRQ', CA: 'CAN' }

export default function JODIPanel() {
  const [summary, setSummary] = useState(undefined)
  const [history, setHistory] = useState({})
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API}/jodi/summary`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setSummary(d)
        // Fetch 3 months history for each top-5 country to compute MoM change
        if (d) {
          Promise.all(
            TOP5.map((c) =>
              fetch(`${API}/jodi/production?country=${c}&limit=3`)
                .then((r) => (r.ok ? r.json() : []))
                .then((rows) => [c, rows])
            )
          ).then((results) => {
            const h = {}
            for (const [c, rows] of results) h[c] = rows
            setHistory(h)
          })
        }
      })
      .catch((e) => {
        console.error('JODIPanel fetch:', e)
        setError(e.message)
      })
  }, [])

  const changes = useMemo(() => {
    const result = {}
    for (const [country, rows] of Object.entries(history)) {
      if (rows.length < 2) continue
      const sorted = [...rows].sort((a, b) => b.date.localeCompare(a.date))
      const curr = sorted[0]?.production
      const prev = sorted[1]?.production
      if (curr != null && prev != null && prev > 0) {
        const diff = curr - prev
        const pct = ((curr - prev) / prev) * 100
        result[country] = { diff, pct, prevDate: sorted[1]?.date }
      }
    }
    return result
  }, [history])

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">
          JODI // FETCH ERROR
        </div>
      </div>
    )

  if (summary === undefined) return <SkeletonCard lines={5} />
  if (!summary || summary.length === 0) return null

  const top5 = summary
    .filter((r) => TOP5.includes(r.country) && r.production != null)
    .sort((a, b) => (b.production || 0) - (a.production || 0))

  if (top5.length === 0) return null

  const maxProd = Math.max(...top5.map((r) => r.production || 0), 1)
  const latestDate = top5[0]?.date || ''

  // Find biggest movers for alert banner
  const bigMovers = Object.entries(changes)
    .filter(([, c]) => Math.abs(c.pct) >= 2)
    .sort((a, b) => Math.abs(b[1].pct) - Math.abs(a[1].pct))

  return (
    <Panel id="jodi" title="GLOBAL OIL PRODUCTION // JODI" info="Monthly production data from top-5 oil producers. Source: JODI Oil World Database (IEA/OPEC/UN)." collapsible headerRight={latestDate && <span className="font-mono text-[9px] text-neutral-600">{latestDate}</span>}>
      <div className="px-4 py-3">

      {/* Production change alerts */}
      {bigMovers.length > 0 && (
        <div className="mb-2 space-y-1">
          {bigMovers.map(([country, c]) => {
            const up = c.pct > 0
            const kbd = Math.abs(c.diff) / 30.44 / 1000
            return (
              <div
                key={country}
                className={`font-mono text-[10px] px-2 py-1 rounded border ${
                  up
                    ? 'border-green-glow/20 bg-green-glow/5 text-green-glow'
                    : 'border-red-400/20 bg-red-400/5 text-red-400'
                }`}
              >
                {FLAG[country]} {SHORT[country] || country}{' '}
                {up ? '+' : ''}{c.pct.toFixed(1)}% MoM
                <span className="text-neutral-500 ml-1">
                  ({up ? '+' : '-'}{kbd.toFixed(1)} Mbd)
                </span>
              </div>
            )
          })}
        </div>
      )}

      <div className="space-y-2">
        {top5.map((r) => {
          const pct = (r.production / maxProd) * 100
          const kbd = r.production / 30.44
          const ch = changes[r.country]
          return (
            <div key={r.country}>
              <div className="flex items-center justify-between mb-0.5">
                <div className="flex items-center gap-1.5">
                  <span className="text-xs">{FLAG[r.country] || ''}</span>
                  <span className="font-mono text-[10px] text-neutral-400">
                    {SHORT[r.country] || r.country}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {ch && (
                    <span
                      className={`font-mono text-[9px] ${
                        ch.pct >= 0 ? 'text-green-glow' : 'text-red-400'
                      }`}
                    >
                      {ch.pct >= 0 ? '+' : ''}
                      {ch.pct.toFixed(1)}%
                    </span>
                  )}
                  <span className="font-mono text-[10px] text-cyan-glow font-semibold">
                    {(kbd / 1000).toFixed(1)}
                    <span className="text-neutral-500 ml-0.5">Mbd</span>
                  </span>
                </div>
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
    </Panel>
  )
}
