import { useState, useEffect } from 'react'
import { InfoPopover } from './Panel'
import WaitlistSignup from './WaitlistSignup'

const API = '/api'

const SEVERITY_DOT = {
  critical: 'bg-red-400',
  warning: 'bg-orange-400',
  info: 'bg-neutral-500',
}

const SEVERITY_TEXT = {
  critical: 'text-red-400',
  warning: 'text-orange-400',
  info: 'text-neutral-400',
}

export default function BriefingPanel() {
  const [briefing, setBriefing] = useState(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch(`${API}/briefing/today`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setBriefing)
      .catch(() => setError(true))
  }, [])

  if (error || !briefing) return null

  const { market_snapshot: market, market_structure: mktStruct, anomalies, fleet_status: fleet, upcoming } = briefing
  const hasAnomalies = anomalies && anomalies.length > 0

  return (
    <div className="border border-border bg-surface p-4 font-mono text-sm">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${hasAnomalies ? 'bg-red-400 animate-pulse' : 'bg-emerald-400'}`} />
          <span className="text-cyan-glow font-bold text-xs tracking-widest">
            OBSYD BRIEFING
          </span>
          <span className="text-neutral-600 text-xs">// {briefing.date}</span>
          <InfoPopover text="Daily summary of key market anomalies. Based on PortWatch transits and FRED price data." />
        </div>
      </div>

      {/* Anomalies — compact, click scrolls to ChokePointMonitor for details */}
      {hasAnomalies ? (
        <div className="mb-3 space-y-1">
          {anomalies.map((a, i) => (
            <button
              key={i}
              onClick={() => document.getElementById('chokepoint-monitor')?.scrollIntoView({ behavior: 'smooth' })}
              className="flex items-center gap-2 w-full text-left hover:bg-white/3 rounded px-1 py-0.5 transition-colors"
            >
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${SEVERITY_DOT[a.severity]}`} />
              <span className={`text-xs font-bold ${SEVERITY_TEXT[a.severity]}`}>
                {a.title}
              </span>
              {a.historical_count > 0 && a.avg_brent_impact_7d != null && (
                <span className={`text-xs ${a.avg_brent_impact_7d > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                  Brent {a.avg_brent_impact_7d > 0 ? '+' : ''}{a.avg_brent_impact_7d}% (n={a.historical_count})
                </span>
              )}
            </button>
          ))}
        </div>
      ) : (
        <div className="text-emerald-400/80 text-xs mb-3 pl-1">
          All chokepoints within normal range
        </div>
      )}

      {/* Fleet status */}
      {fleet && fleet.tankers_global > 0 && (
        <div className="text-neutral-400 text-xs mb-3 pl-4">
          Global fleet: {fleet.total_vessels_global?.toLocaleString()} vessels, {fleet.tankers_global?.toLocaleString()} tankers
        </div>
      )}

      {/* Market bar */}
      {market && Object.keys(market).length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs border-t border-border pt-2">
          {['wti', 'brent', 'ng', 'jkm', 'ttf', 'gold'].map((key) => {
            const m = market[key]
            if (!m) return null
            const up = m.change_pct >= 0
            return (
              <span key={key} className="text-neutral-300">
                {key.toUpperCase()}{' '}
                <span className="text-neutral-100">${m.price != null ? m.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'}</span>{' '}
                <span className={up ? 'text-emerald-400' : 'text-red-400'}>
                  ({up ? '+' : ''}{m.change_pct != null ? m.change_pct.toFixed(1) : '?'}%)
                </span>
              </span>
            )
          })}
          {mktStruct?.summary && mktStruct.summary !== 'unavailable' && (() => {
            const s = mktStruct.summary
            const cls = s === 'backwardation' ? 'text-red-400' : s === 'contango' ? 'text-emerald-400' : 'text-neutral-500'
            return (
              <span className={cls}>
                {s.toUpperCase()}
                {mktStruct.curves?.WTI?.spread_pct != null && ` (${mktStruct.curves.WTI.spread_pct > 0 ? '+' : ''}${mktStruct.curves.WTI.spread_pct.toFixed(1)}%)`}
              </span>
            )
          })()}
          {market.sentiment_score != null && (
            <span className="text-neutral-500">
              Sentiment: {market.sentiment_score}/10
            </span>
          )}
          {upcoming?.eia_report && (
            <span className="text-neutral-600">
              Next EIA: {upcoming.eia_report}
            </span>
          )}
        </div>
      )}

      {/* Waitlist signup */}
      <div className="mt-3">
        <WaitlistSignup />
      </div>
    </div>
  )
}
