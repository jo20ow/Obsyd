import { useState, useEffect, useMemo } from 'react'
import Panel from './Panel'

const API = '/api'

const CHOKEPOINTS = [
  { key: 'hormuz', label: 'HORMUZ' },
  { key: 'suez', label: 'SUEZ' },
  { key: 'cape', label: 'CAPE' },
]

function brentColor(pct) {
  if (pct == null) return 'text-neutral-500'
  if (pct > 3) return 'text-red-400'
  if (pct > 0) return 'text-orange-400'
  if (pct < -3) return 'text-green-glow'
  if (pct < 0) return 'text-emerald-400'
  return 'text-neutral-400'
}

function severityBar(pct) {
  const abs = Math.abs(pct)
  if (abs >= 60) return 'bg-red-500'
  if (abs >= 50) return 'bg-orange-500'
  return 'bg-yellow-500'
}

export default function EventTimeline() {
  const [selected, setSelected] = useState('hormuz')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/signals/historical?chokepoint=${selected}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setData(d)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [selected])

  const anomalies = useMemo(() => {
    if (!data?.anomalies) return []
    return [...data.anomalies].sort((a, b) =>
      b.start_date.localeCompare(a.start_date)
    )
  }, [data])

  const avgImpact = useMemo(() => {
    const valid = anomalies.filter((a) => a.brent_change_7d_pct != null)
    if (valid.length === 0) return null
    return valid.reduce((s, a) => s + a.brent_change_7d_pct, 0) / valid.length
  }, [anomalies])

  return (
    <Panel id="event-timeline" title="HISTORICAL ANOMALIES // DISRUPTIONS" info="Historical transit anomalies (>40% drop) since 2019 with Brent price impact." headerRight={<div className="flex items-center gap-1">{CHOKEPOINTS.map((cp) => (<button key={cp.key} onClick={() => setSelected(cp.key)} className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${selected === cp.key ? 'bg-cyan-glow/20 text-cyan-glow' : 'text-neutral-600 hover:text-neutral-400'}`}>{cp.label}</button>))}</div>}>
      <div className="px-4 py-3">

      {loading && (
        <div className="font-mono text-[10px] text-neutral-600 animate-pulse py-8 text-center">
          LOADING ...
        </div>
      )}

      {!loading && anomalies.length === 0 && (
        <div className="font-mono text-[10px] text-neutral-600 py-4 text-center">
          Keine Anomalien (&gt;40% Drop) seit 2019
        </div>
      )}

      {!loading && anomalies.length > 0 && (
        <>
          {/* Summary bar */}
          <div className="flex items-center gap-4 mb-3 font-mono text-[10px]">
            <span className="text-neutral-500">
              {data.date_range}
            </span>
            <span className="text-cyan-glow">
              {anomalies.length} Events
            </span>
            {avgImpact != null && (
              <span className={brentColor(avgImpact)}>
                Brent avg 7d: {avgImpact > 0 ? '+' : ''}{avgImpact.toFixed(1)}%
              </span>
            )}
          </div>

          {/* Timeline */}
          <div className="relative border-l border-border ml-2 space-y-0">
            {anomalies.map((a, i) => (
              <div key={i} className="relative pl-5 pb-3 group">
                {/* Dot */}
                <div
                  className={`absolute left-[-5px] top-1.5 w-2.5 h-2.5 rounded-full border-2 border-[#0d0d14] ${severityBar(a.max_drop_pct)}`}
                />

                {/* Content */}
                <div className="font-mono text-[10px]">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-neutral-300 font-bold">
                      {a.start_date}
                    </span>
                    <span className="text-red-400">
                      {a.max_drop_pct.toFixed(0)}%
                    </span>
                    <span className="text-neutral-600">
                      ({a.start_value} vs {a.avg_30d.toFixed(0)} avg)
                    </span>
                    {a.duration_days > 1 && (
                      <span className="text-neutral-600">
                        {a.duration_days}d
                      </span>
                    )}
                  </div>

                  {/* Brent impact */}
                  <div className="flex items-center gap-3 mt-0.5">
                    <span className="text-neutral-600">
                      Brent ${a.brent_at_start?.toFixed(2)}
                    </span>
                    {a.brent_change_7d_pct != null && (
                      <span className={brentColor(a.brent_change_7d_pct)}>
                        7d: {a.brent_change_7d_pct > 0 ? '+' : ''}
                        {a.brent_change_7d_pct.toFixed(1)}%
                      </span>
                    )}
                    {a.brent_change_30d_pct != null && (
                      <span className={brentColor(a.brent_change_30d_pct)}>
                        30d: {a.brent_change_30d_pct > 0 ? '+' : ''}
                        {a.brent_change_30d_pct.toFixed(1)}%
                      </span>
                    )}
                  </div>

                  {/* Disruption context */}
                  {a.disruption_context?.length > 0 && (
                    <div className="text-orange-400/70 mt-0.5">
                      {a.disruption_context.join(' / ')}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      </div>
    </Panel>
  )
}
