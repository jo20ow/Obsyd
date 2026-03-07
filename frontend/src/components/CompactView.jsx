import { useState, useEffect } from 'react'

const API = '/api'

const SEVERITY_DOT = {
  critical: 'bg-red-400 animate-pulse',
  warning: 'bg-orange-400',
  info: 'bg-neutral-500',
}

const SEVERITY_TEXT = {
  critical: 'text-red-400',
  warning: 'text-orange-400',
  info: 'text-neutral-400',
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00Z')
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).toUpperCase()
}

export default function CompactView({ onSwitchToFull }) {
  const [briefing, setBriefing] = useState(null)
  const [headlines, setHeadlines] = useState(null)
  const [rerouting, setRerouting] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch(`${API}/briefing/today`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API}/sentiment/headlines`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API}/signals/rerouting-index`).then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([b, h, r]) => {
        setBriefing(b)
        setHeadlines(h?.articles || [])
        setRerouting(r)
      })
      .catch((e) => console.error('CompactView fetch:', e))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-cyan-glow font-mono text-sm animate-pulse">
          OBSYD // LOADING ...
        </div>
      </div>
    )
  }

  const market = briefing?.market_snapshot
  const mktStruct = briefing?.market_structure
  const anomalies = briefing?.anomalies || []
  const hasAnomalies = anomalies.length > 0
  const dateStr = briefing?.date ? formatDate(briefing.date) : new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).toUpperCase()

  // Rerouting state
  const reroutingAvail = rerouting?.available
  const reroutingCurrent = rerouting?.current
  const reroutingState = reroutingCurrent?.state
  const reroutingPct = reroutingCurrent?.ratio_pct

  const STATE_LABELS = {
    normal: { text: 'text-green-glow', label: 'NORMAL' },
    elevated: { text: 'text-yellow-400', label: 'ELEVATED' },
    high_rerouting: { text: 'text-red-400', label: 'HIGH' },
  }

  // Market structure label
  const structSummary = mktStruct?.summary
  const structCls = structSummary === 'backwardation' ? 'text-red-400' : structSummary === 'contango' ? 'text-emerald-400' : 'text-neutral-500'

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="border border-border bg-surface w-full max-w-xl font-mono">
        {/* Title bar */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2">
            <span className="text-cyan-glow font-bold text-sm tracking-widest">OBSYD</span>
            <span className="text-neutral-600 text-[10px]">// {dateStr}</span>
          </div>
          <span className={`w-2 h-2 rounded-full ${hasAnomalies ? 'bg-red-400 animate-pulse' : 'bg-emerald-400'}`} />
        </div>

        {/* Chokepoint status */}
        <div className="px-4 py-3 border-b border-border">
          {hasAnomalies ? (
            <div className="space-y-2">
              {anomalies.map((a, i) => (
                <div key={i}>
                  <div className="flex items-start gap-2">
                    <span className={`w-2 h-2 rounded-full mt-1 shrink-0 ${SEVERITY_DOT[a.severity]}`} />
                    <div className="min-w-0">
                      <div className={`text-xs font-bold ${SEVERITY_TEXT[a.severity]}`}>
                        {a.severity.toUpperCase()}: {a.title}
                      </div>
                      <div className="text-neutral-500 text-[10px] mt-0.5">
                        {a.current_value} ships vs. {a.average_30d} avg (30d)
                      </div>
                      {a.historical_count > 0 && (
                        <div className="text-neutral-500 text-[10px] mt-0.5">
                          {a.historical_count} similar events since 2019
                          {a.avg_brent_impact_7d != null && (
                            <span className={a.avg_brent_impact_7d > 0 ? ' text-red-400' : ' text-emerald-400'}>
                              {' '} — Brent avg {a.avg_brent_impact_7d > 0 ? '+' : ''}{a.avg_brent_impact_7d}% within 7d
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
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span className="text-emerald-400/80 text-xs">All chokepoints within normal range</span>
            </div>
          )}
        </div>

        {/* Market bar */}
        {market && Object.keys(market).length > 0 && (
          <div className="px-4 py-3 border-b border-border">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
              {['wti', 'brent'].map((key) => {
                const m = market[key]
                if (!m) return null
                const up = m.change_pct >= 0
                return (
                  <span key={key} className="text-neutral-300">
                    {key.toUpperCase()}{' '}
                    <span className="text-neutral-100 font-bold">
                      ${m.price != null ? m.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'}
                    </span>{' '}
                    <span className={up ? 'text-emerald-400' : 'text-red-400'}>
                      ({up ? '+' : ''}{m.change_pct != null ? m.change_pct.toFixed(1) : '?'}%)
                    </span>
                  </span>
                )
              })}
            </div>
            <div className="flex flex-wrap gap-x-3 mt-1.5 text-[10px]">
              {structSummary && structSummary !== 'unavailable' && (
                <span className={`font-bold ${structCls}`}>
                  {structSummary.toUpperCase()}
                  {mktStruct?.curves?.WTI?.spread_pct != null && ` (${mktStruct.curves.WTI.spread_pct > 0 ? '+' : ''}${mktStruct.curves.WTI.spread_pct.toFixed(1)}%)`}
                </span>
              )}
              {reroutingAvail && reroutingPct != null && (
                <span className="text-neutral-500">
                  Rerouting{' '}
                  <span className={`font-bold ${(STATE_LABELS[reroutingState] || STATE_LABELS.normal).text}`}>
                    {reroutingPct}%{' '}
                    {(STATE_LABELS[reroutingState] || STATE_LABELS.normal).label}
                  </span>
                </span>
              )}
              {market.sentiment_score != null && (
                <span className="text-neutral-500">
                  Sentiment {market.sentiment_score}/10
                </span>
              )}
            </div>
          </div>
        )}

        {/* Headlines */}
        {headlines && headlines.length > 0 && (
          <div className="px-4 py-3 border-b border-border">
            <div className="text-[10px] text-neutral-600 tracking-wider mb-2">HEADLINES</div>
            <div className="space-y-1.5">
              {headlines.slice(0, 5).map((h, i) => (
                <a
                  key={i}
                  href={h.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block group"
                >
                  <div className="text-[11px] text-neutral-400 group-hover:text-cyan-glow transition-colors leading-tight">
                    {h.title}
                  </div>
                  <div className="text-[9px] text-neutral-600 mt-0.5">
                    {h.domain}
                  </div>
                </a>
              ))}
            </div>
          </div>
        )}

        {/* Full Dashboard button */}
        <div className="px-4 py-3 flex justify-end">
          <button
            onClick={onSwitchToFull}
            className="text-[10px] text-cyan-glow/70 hover:text-cyan-glow tracking-wider transition-colors border border-cyan-glow/20 hover:border-cyan-glow/50 px-3 py-1.5"
          >
            FULL DASHBOARD →
          </button>
        </div>
      </div>
    </div>
  )
}
