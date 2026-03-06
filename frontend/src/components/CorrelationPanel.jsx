import { useState, useEffect } from 'react'

const API = '/api'

function corrColor(r) {
  const abs = Math.abs(r)
  if (abs > 0.5) return r > 0 ? 'text-green-glow' : 'text-red-400'
  if (abs > 0.3) return r > 0 ? 'text-green-glow/70' : 'text-red-400/70'
  return 'text-neutral-500'
}

function corrBar(r) {
  const abs = Math.abs(r)
  const width = Math.min(abs * 100, 100)
  const color = r > 0 ? 'bg-green-glow/40' : 'bg-red-500/40'
  return { width: `${width}%`, color }
}

function impactArrow(pct) {
  if (pct > 0) return { text: `+${pct.toFixed(1)}%`, cls: 'text-red-400' }
  if (pct < 0) return { text: `${pct.toFixed(1)}%`, cls: 'text-green-glow' }
  return { text: '0.0%', cls: 'text-neutral-500' }
}

function formatDateShort(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00Z')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function generateInsight(cp) {
  const { chokepoint, current_anomaly_pct, delta_correlation, best_delta_lag_days, avg_price_impact_pct, n_impact_events } = cp
  const name = chokepoint.split(' ').pop()

  if (n_impact_events === 0) {
    return `${name}: insufficient disruption events (>30% drops) for impact analysis`
  }

  const dir = current_anomaly_pct < -10 ? 'drop' : current_anomaly_pct > 10 ? 'surge' : 'stable'
  const corrDir = delta_correlation < 0 ? 'inversely' : 'positively'

  if (dir === 'drop' && avg_price_impact_pct > 0) {
    return `${name} ${current_anomaly_pct.toFixed(0)}% → historically correlates with +${avg_price_impact_pct.toFixed(1)}% Brent within 7d (n=${n_impact_events})`
  }
  if (dir === 'surge' && avg_price_impact_pct < 0) {
    return `${name} +${current_anomaly_pct.toFixed(0)}% → Brent typically ${avg_price_impact_pct.toFixed(1)}% when traffic normalizes`
  }
  if (dir !== 'stable') {
    return `${name} ${current_anomaly_pct > 0 ? '+' : ''}${current_anomaly_pct.toFixed(0)}% → delta ${corrDir} correlated (Δr=${delta_correlation.toFixed(2)}, lag ${best_delta_lag_days}d)`
  }

  return `${name} stable — Δ ${corrDir} correlated (r=${delta_correlation.toFixed(2)}), avg impact ${avg_price_impact_pct > 0 ? '+' : ''}${avg_price_impact_pct.toFixed(1)}% on >30% drops`
}

function CurrentEventBanner({ event, chokepoint }) {
  if (!event) return null
  const name = chokepoint.split(' ').pop()
  const brentDir = event.brent_change_pct >= 0
  return (
    <div className="px-4 py-1.5 border-b border-red-500/20 bg-red-500/5 flex items-center gap-2">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
      <span className="font-mono text-[9px] text-red-400 tracking-wider">ACTIVE EVENT</span>
      <span className="font-mono text-[10px] text-neutral-300">
        {name} {event.anomaly_pct > 0 ? '+' : ''}{event.anomaly_pct}% since {formatDateShort(event.event_start)}
      </span>
      <span className="font-mono text-[9px] text-neutral-500">({event.duration_days}d)</span>
      <span className="font-mono text-[9px] text-neutral-600 mx-1">//</span>
      <span className="font-mono text-[10px] text-neutral-400">
        Brent ${event.brent_at_start} → ${event.brent_current}
      </span>
      <span className={`font-mono text-[10px] font-bold ${brentDir ? 'text-red-400' : 'text-green-glow'}`}>
        ({brentDir ? '+' : ''}{event.brent_change_pct}%)
      </span>
    </div>
  )
}

export default function CorrelationPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/signals/correlation`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.correlations) setData(d.correlations)
        setLoading(false)
      })
      .catch((e) => { console.error('CorrelationPanel fetch:', e); setLoading(false) })
  }, [])

  if (loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-6">
        <div className="font-mono text-[10px] text-neutral-600 animate-pulse text-center">
          CORRELATION ENGINE // COMPUTING ...
        </div>
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-6">
        <div className="font-mono text-[10px] text-neutral-600 text-center">
          CORRELATION // NO DATA — run backfill + oil price fetch first
        </div>
      </div>
    )
  }

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div className="font-mono text-[10px] text-neutral-600 tracking-wider">
          CHOKEPOINT → BRENT CORRELATION // 365D ANALYSIS
        </div>
        <div className="font-mono text-[9px] text-neutral-700">
          PEARSON r + LAG
        </div>
      </div>

      {/* Current event banners */}
      {data.filter((cp) => cp.current_event).map((cp) => (
        <CurrentEventBanner key={`ev-${cp.portid}`} event={cp.current_event} chokepoint={cp.chokepoint} />
      ))}

      {/* Table header */}
      <div className="grid grid-cols-12 gap-1 px-4 py-1.5 border-b border-border/50 font-mono text-[9px] text-neutral-600">
        <div className="col-span-2">CHOKEPOINT</div>
        <div className="col-span-1 text-right">ANOM</div>
        <div className="col-span-3 text-center">LEVEL r / DELTA Δr</div>
        <div className="col-span-2 text-center">BEST LAG</div>
        <div className="col-span-2 text-center">Δ LAG</div>
        <div className="col-span-2 text-right">7D IMPACT</div>
      </div>

      {/* Rows */}
      {data.map((cp) => {
        const deltaBar = corrBar(cp.delta_correlation)
        const impact = impactArrow(cp.avg_price_impact_pct)
        return (
          <div key={cp.portid}>
            <div className="grid grid-cols-12 gap-1 px-4 py-2 border-b border-border/30 items-center">
              {/* Name */}
              <div className="col-span-2">
                <span className="font-mono text-[10px] text-neutral-400">
                  {cp.chokepoint.toUpperCase()}
                </span>
                <div className="font-mono text-[8px] text-neutral-700">
                  {cp.data_points}d
                </div>
              </div>

              {/* Current anomaly */}
              <div className="col-span-1 text-right">
                <span className={`font-mono text-[11px] font-bold ${
                  cp.current_anomaly_pct > 10 ? 'text-green-glow' :
                  cp.current_anomaly_pct < -10 ? 'text-red-400' :
                  'text-neutral-500'
                }`}>
                  {cp.current_anomaly_pct > 0 ? '+' : ''}{cp.current_anomaly_pct.toFixed(0)}%
                </span>
              </div>

              {/* Level r + Delta r with bar */}
              <div className="col-span-3">
                <div className="flex items-center gap-1.5">
                  <span className={`font-mono text-[10px] ${corrColor(cp.correlation)} w-10 text-right`}>
                    {cp.correlation > 0 ? '+' : ''}{cp.correlation.toFixed(2)}
                  </span>
                  <span className="font-mono text-[8px] text-neutral-700">/</span>
                  <div className="flex-1 h-1.5 bg-surface-light rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${deltaBar.color}`}
                      style={{ width: deltaBar.width }}
                    />
                  </div>
                  <span className={`font-mono text-xs font-bold ${corrColor(cp.delta_correlation)} w-12 text-right`}>
                    {cp.delta_correlation > 0 ? '+' : ''}{cp.delta_correlation.toFixed(2)}
                  </span>
                </div>
              </div>

              {/* Best level lag */}
              <div className="col-span-2 text-center">
                <span className="font-mono text-[10px] text-cyan-glow">
                  {cp.best_lag_days}d
                </span>
                <span className="font-mono text-[9px] text-neutral-600 ml-0.5">
                  r={cp.best_lag_correlation.toFixed(2)}
                </span>
              </div>

              {/* Best delta lag */}
              <div className="col-span-2 text-center">
                <span className="font-mono text-[10px] text-cyan-glow">
                  {cp.best_delta_lag_days}d
                </span>
                <span className="font-mono text-[9px] text-neutral-600 ml-0.5">
                  Δr={cp.best_delta_lag_correlation.toFixed(2)}
                </span>
              </div>

              {/* Price impact */}
              <div className="col-span-2 text-right">
                <span className={`font-mono text-xs font-bold ${impact.cls}`}>
                  {impact.text}
                </span>
                {cp.n_impact_events > 0 && (
                  <div className="font-mono text-[8px] text-neutral-700">
                    n={cp.n_impact_events}
                  </div>
                )}
              </div>
            </div>

            {/* Insight line */}
            <div className="px-4 py-1 border-b border-border/30 bg-surface-light/30">
              <span className="font-mono text-[9px] text-neutral-500 italic">
                {generateInsight(cp)}
              </span>
            </div>
          </div>
        )
      })}

      <div className="px-4 py-1.5 font-mono text-[8px] text-neutral-700">
        Based on vessel transit counts, not barrel volumes // Level r: n_tanker vs Brent // Δr: day-over-day changes // Impact: avg Brent 7d after &gt;30% transit drop
      </div>
    </div>
  )
}
