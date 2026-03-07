import { useState, useEffect } from 'react'
import Panel from './Panel'

const API = '/api'

function formatHours(h) {
  if (h < 24) return `${h.toFixed(0)}h`
  return `${(h / 24).toFixed(1)}d`
}

export default function STSPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/vessels/sts`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const hasCandidates = data?.sts_candidate_count > 0
  const hasDark = data?.dark_vessel_count > 0
  const hasPairs = data?.proximity_pair_count > 0
  const hasAnything = hasCandidates || hasDark || hasPairs

  const statusText = !data ? '—' :
    hasCandidates || hasPairs ? `${data.sts_candidate_count} STS / ${data.dark_vessel_count} DARK` :
    hasDark ? `${data.dark_vessel_count} DARK` : 'CLEAR'

  const statusColor = hasCandidates || hasPairs ? 'text-orange-400' :
    hasDark ? 'text-yellow-400' : 'text-green-glow'

  return (
    <Panel
      id="sts-detection"
      title="STS / DARK ACTIVITY"
      info="Ship-to-Ship transfer detection at known hotspots (Laconian Gulf, Fujairah, Malaysia EOPL, Lomé). Dark = AIS signal gap >48h."
      collapsible
      headerRight={
        <span className={`font-mono text-[10px] font-bold ${statusColor}`}>
          {statusText}
        </span>
      }
    >
      <div className="px-4 py-3">
        {loading && (
          <div className="font-mono text-[10px] text-neutral-600 animate-pulse py-4 text-center">
            SCANNING ...
          </div>
        )}

        {!loading && !hasAnything && (
          <div className="font-mono text-[10px] text-neutral-600 py-4 text-center">
            No STS candidates or dark vessels detected
          </div>
        )}

        {/* STS Candidates */}
        {hasCandidates && (
          <div className="mb-3">
            <div className="font-mono text-[9px] text-orange-400 tracking-wider mb-1.5">
              STS CANDIDATES — ANCHORED IN HOTSPOT
            </div>
            <div className="space-y-1">
              {data.sts_candidates.map((v) => (
                <div key={v.mmsi} className="flex items-center gap-2 py-1 border-b border-border/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-orange-400 shrink-0" />
                  <span className="font-mono text-[10px] text-neutral-300 font-bold min-w-[100px]">
                    {v.ship_name || v.mmsi}
                  </span>
                  <span className={`font-mono text-[9px] px-1 py-0.5 rounded ${
                    v.class === 'VLCC' ? 'bg-red-500/15 text-red-400' :
                    v.class === 'Suezmax' ? 'bg-orange-500/15 text-orange-400' :
                    'bg-neutral-800 text-neutral-500'
                  }`}>
                    {v.class}
                  </span>
                  <span className="font-mono text-[9px] text-neutral-500">
                    {v.sts_display}
                  </span>
                  <span className="font-mono text-[9px] text-neutral-600 ml-auto">
                    {v.sog.toFixed(1)} kn · {formatHours(v.age_hours)} ago
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Proximity Pairs */}
        {hasPairs && (
          <div className="mb-3">
            <div className="font-mono text-[9px] text-red-400 tracking-wider mb-1.5">
              PROXIMITY PAIRS — {'<'}500M IN STS ZONE
            </div>
            <div className="space-y-1.5">
              {data.proximity_pairs.map((p, i) => (
                <div key={i} className="border border-red-500/20 bg-red-500/5 rounded px-3 py-2">
                  <div className="flex items-center gap-2 font-mono text-[10px]">
                    <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" />
                    <span className="text-neutral-300 font-bold">{p.vessel_1.ship_name}</span>
                    <span className="text-neutral-600">↔</span>
                    <span className="text-neutral-300 font-bold">{p.vessel_2.ship_name}</span>
                    <span className="text-red-400 ml-auto">{(p.distance_km * 1000).toFixed(0)}m</span>
                  </div>
                  <div className="font-mono text-[9px] text-neutral-600 mt-0.5">
                    {p.vessel_1.class} + {p.vessel_2.class} · {p.hotspot}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Dark Vessels */}
        {hasDark && (
          <div>
            <div className="font-mono text-[9px] text-yellow-400 tracking-wider mb-1.5">
              DARK VESSELS — AIS GAP {'>'}48H
            </div>
            <div className="space-y-0.5">
              {data.dark_vessels.slice(0, 10).map((v) => (
                <div key={v.mmsi} className="flex items-center gap-2 py-1 border-b border-border/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-yellow-400/60 shrink-0" />
                  <span className="font-mono text-[10px] text-neutral-400 min-w-[100px]">
                    {v.ship_name || v.mmsi}
                  </span>
                  <span className="font-mono text-[9px] text-neutral-600">
                    {v.class}
                  </span>
                  <span className="font-mono text-[9px] text-neutral-600">
                    last: {v.last_zone}
                  </span>
                  {v.last_in_sts_hotspot && (
                    <span className="font-mono text-[8px] text-orange-400/70 px-1 bg-orange-500/10 rounded">
                      STS ZONE
                    </span>
                  )}
                  <span className="font-mono text-[10px] text-yellow-400 ml-auto font-bold">
                    {formatHours(v.dark_hours)}
                  </span>
                </div>
              ))}
              {data.dark_vessel_count > 10 && (
                <div className="font-mono text-[9px] text-neutral-600 pt-1">
                  + {data.dark_vessel_count - 10} more
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="px-4 py-1.5 border-t border-border/50 font-mono text-[8px] text-neutral-700">
        Hotspots: Laconian Gulf, Fujairah, Malaysia EOPL, Lomé, Kalamata // SOG {'<'}1kn = anchored // Dark = no signal {'>'}48h
      </div>
    </Panel>
  )
}
