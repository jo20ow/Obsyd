import { useState, useEffect } from 'react'

const API = '/api'

const SEVERITY_STYLES = {
  critical: 'border-red-500/40 bg-red-500/5',
  warning: 'border-orange-500/40 bg-orange-500/5',
  info: 'border-neutral-600 bg-neutral-800/30',
}

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
        </div>
        {upcoming?.eia_report && (
          <span className="text-neutral-600 text-xs">
            Next EIA: {upcoming.eia_report}
          </span>
        )}
      </div>

      {/* Anomalies */}
      {hasAnomalies ? (
        <div className="space-y-2 mb-3">
          {anomalies.map((a, i) => (
            <div
              key={i}
              className={`border rounded px-3 py-2 ${SEVERITY_STYLES[a.severity]}`}
            >
              <div className="flex items-start gap-2">
                <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${SEVERITY_DOT[a.severity]}`} />
                <div className="min-w-0">
                  <button
                    onClick={() => {
                      const el = document.getElementById('chokepoint-monitor')
                      el?.scrollIntoView({ behavior: 'smooth' })
                    }}
                    className={`font-bold text-left hover:underline ${SEVERITY_TEXT[a.severity]}`}
                  >
                    {a.severity.toUpperCase()}: {a.title}
                  </button>
                  <div className="text-neutral-400 text-xs mt-0.5">
                    {a.current_value} ships vs. {a.average_30d} avg (30d)
                  </div>
                  {a.historical_count > 0 && (
                    <div className="text-neutral-500 text-xs mt-0.5">
                      {a.historical_count} similar events since 2019
                      {a.avg_brent_impact_7d != null && (
                        <span className={a.avg_brent_impact_7d > 0 ? 'text-red-400' : 'text-emerald-400'}>
                          {' '}— Brent avg {a.avg_brent_impact_7d > 0 ? '+' : ''}{a.avg_brent_impact_7d}% (7d after)
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-emerald-400/80 text-xs mb-3 pl-4">
          All chokepoints within normal range
        </div>
      )}

      {/* Fleet + Anchored Vessels */}
      {fleet && (fleet.tankers_global > 0 || fleet.anchored_alerts?.length > 0) && (
        <div className="text-neutral-400 text-xs mb-3 pl-4 space-y-0.5">
          {fleet.tankers_global > 0 && (
            <div>
              Global fleet: {fleet.total_vessels_global?.toLocaleString()} vessels, {fleet.tankers_global?.toLocaleString()} tankers
            </div>
          )}
          {fleet.anchored_alerts?.map((a, i) => (
            <div key={i} className="text-neutral-500">
              {a.title}
            </div>
          ))}
        </div>
      )}

      {/* Market bar */}
      {market && Object.keys(market).length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs border-t border-border pt-2">
          {['wti', 'brent', 'ng', 'gold'].map((key) => {
            const m = market[key]
            if (!m) return null
            const up = m.change_pct >= 0
            return (
              <span key={key} className="text-neutral-300">
                {key.toUpperCase()}{' '}
                <span className="text-neutral-100">${m.price?.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>{' '}
                <span className={up ? 'text-emerald-400' : 'text-red-400'}>
                  ({up ? '+' : ''}{m.change_pct?.toFixed(1)}%)
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
                {mktStruct.curves?.WTI && ` (${mktStruct.curves.WTI.spread_pct > 0 ? '+' : ''}${mktStruct.curves.WTI.spread_pct.toFixed(1)}%)`}
              </span>
            )
          })()}
          {market.sentiment_score != null && (
            <span className="text-neutral-500">
              Sentiment: {market.sentiment_score}/10
            </span>
          )}
        </div>
      )}
    </div>
  )
}
