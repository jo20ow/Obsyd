import { useState, useEffect } from 'react'
import WaitlistSignup from './WaitlistSignup'

const API = '/api'

const SEV_DOT = {
  critical: 'bg-red-400',
  warning: 'bg-orange-400',
  info: 'bg-neutral-500',
}
const SEV_TEXT = {
  critical: 'text-red-400',
  warning: 'text-orange-400',
  info: 'text-neutral-400',
}

export default function BriefingPanel() {
  const [b, setB] = useState(null)

  useEffect(() => {
    fetch(`${API}/briefing/today`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setB)
      .catch(() => {})
  }, [])

  if (!b) return null

  const { market_snapshot: mkt, anomalies, fleet_status: fleet, upcoming } = b
  const top = anomalies?.[0]

  return (
    <div className="border border-border bg-surface px-4 py-2.5 font-mono text-xs space-y-1">
      {/* Line 1: Top Alert */}
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${top ? SEV_DOT[top.severity] : 'bg-emerald-400'}`} />
        {top ? (
          <>
            <span className={`font-bold ${SEV_TEXT[top.severity]}`}>{top.title}</span>
            {anomalies.length > 1 && <span className="text-neutral-600">+{anomalies.length - 1} more</span>}
          </>
        ) : (
          <span className="text-emerald-400/80">All chokepoints within normal range</span>
        )}
      </div>

      {/* Line 2: Fleet */}
      {fleet?.tankers_global > 0 && (
        <div className="text-neutral-400 pl-3.5">
          Global fleet: {fleet.total_vessels_global?.toLocaleString()} vessels, {fleet.tankers_global?.toLocaleString()} tankers
        </div>
      )}

      {/* Line 3: Prices inline */}
      {mkt && (
        <div className="flex flex-wrap gap-x-3 pl-3.5 text-[10px] md:text-xs">
          {['wti', 'brent', 'ng', 'ttf', 'gold'].map((k) => {
            const p = mkt[k]
            if (!p?.price) return null
            const up = (p.change_pct || 0) >= 0
            return (
              <span key={k} className="text-neutral-300 whitespace-nowrap">
                <span className="text-neutral-500">{k.toUpperCase()}</span>{' '}
                ${p.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}{' '}
                <span className={up ? 'text-emerald-400' : 'text-red-400'}>
                  {up ? '+' : ''}{p.change_pct?.toFixed(1)}%
                </span>
              </span>
            )
          })}
        </div>
      )}

      {/* Line 4: Sentiment + Next EIA */}
      <div className="text-neutral-500 pl-3.5">
        {mkt?.sentiment_score != null && (
          <>
            Sentiment:{' '}
            <span className={
              mkt.sentiment_score >= 7 ? 'text-red-400' : mkt.sentiment_score >= 4 ? 'text-orange-400' : 'text-emerald-400'
            }>{mkt.sentiment_score}/10</span>
          </>
        )}
        {mkt?.sentiment_score != null && upcoming?.eia_report && <span className="text-neutral-700"> · </span>}
        {upcoming?.eia_report && <>Next EIA: {upcoming.eia_report}</>}
      </div>

      {/* Waitlist */}
      <div className="pt-1.5">
        <WaitlistSignup />
      </div>
    </div>
  )
}
