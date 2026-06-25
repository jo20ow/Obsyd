import { useState, useEffect } from 'react'
import Panel from './Panel'

const API = '/api'

const SEV_COLORS = {
  CRITICAL: { text: 'text-red-400', bg: 'bg-red-400/10', border: 'border-red-400/30', dot: 'bg-red-400' },
  HIGH: { text: 'text-orange-400', bg: 'bg-orange-400/10', border: 'border-orange-400/30', dot: 'bg-orange-400' },
  MODERATE: { text: 'text-yellow-400', bg: 'bg-yellow-400/10', border: 'border-yellow-400/30', dot: 'bg-yellow-400' },
  LOW: { text: 'text-green-glow', bg: 'bg-green-glow/10', border: 'border-green-glow/30', dot: 'bg-green-glow' },
}

function ScoreBadge({ score, severity }) {
  const c = SEV_COLORS[severity] || SEV_COLORS.LOW
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono font-bold border ${c.border} ${c.bg} ${c.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      Score: {score?.toFixed(0)}/100 {severity}
    </span>
  )
}


export default function MarketReportPanel() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/analytics/market-report`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { setReport(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <Panel title="MARKET INTELLIGENCE" info="AI-generated narrative analysis combining AIS vessel tracking, chokepoint disruptions, market structure, and historical precedent.">
        <div className="flex items-center gap-2 py-4">
          <div className="w-1.5 h-1.5 rounded-full bg-cyan-glow/50 animate-pulse" />
          <span className="font-mono text-[10px] text-neutral-500 animate-pulse">Generating report...</span>
        </div>
      </Panel>
    )
  }

  if (!report?.available) return null

  const { severity, disruption_score, sections,
          signals_count, historical_events_compared, generated_at } = report

  // Timestamp
  const genTime = generated_at ? new Date(generated_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }) : null

  return (
    <Panel
      title="MARKET INTELLIGENCE"
      info="AI-generated narrative analysis combining AIS vessel tracking, chokepoint disruptions, market structure, and historical precedent. Template-rotated daily. Refreshes every 30 min."
      badge={<span className="text-[9px] font-mono text-cyan-glow/60 border border-cyan-glow/20 px-1.5 py-0.5 rounded">LIVE</span>}
    >
      {/* Score badge */}
      <div className="mb-3">
        <ScoreBadge score={disruption_score} severity={severity} />
      </div>

      {/* Full narrative — free for everyone (5 flowing paragraphs) */}
      <div className="space-y-3">
        {['catalyst', 'historical', 'physical', 'market', 'outlook'].map((key) => {
          const text = sections?.[key]
          if (!text) return null
          return (
            <div key={key} className="font-mono text-[11px] text-neutral-200 leading-relaxed">
              {text}
            </div>
          )
        })}
      </div>

      {/* Footer */}
      <div className="mt-3 pt-2 border-t border-border/20 font-mono text-[9px] text-neutral-600 flex flex-wrap gap-x-3">
        <span>{signals_count} signals analyzed</span>
        {historical_events_compared > 0 && <span>{historical_events_compared} historical events compared</span>}
        {genTime && <span>Generated {genTime}</span>}
        <span>Refreshes every 30 min</span>
      </div>
    </Panel>
  )
}
