import { useState, useEffect } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

function changeColor(pct) {
  if (pct == null) return 'text-neutral-400'
  if (pct >= 5) return 'text-red-400'
  if (pct <= -5) return 'text-green-glow'
  return 'text-neutral-300'
}

export function EIAPredictionMini() {
  const { data } = useFetchWithError(`${API}/analytics/eia-prediction`)

  if (!data?.available) return null

  const pred = data.current
  const changePct = pred.tanker_change_pct

  return (
    <div className="font-mono text-[10px] bg-neutral-900/30 border border-border/30 rounded px-3 py-2 mt-1">
      <div className="flex items-center justify-between">
        <span className="text-neutral-500">
          Houston tankers (7d avg):{' '}
          <span className="font-bold text-neutral-200">{pred.tanker_count}</span>
          {changePct != null && (
            <span className={`ml-2 ${changeColor(changePct)}`}>
              ({changePct >= 0 ? '+' : ''}{changePct}% vs 30d)
            </span>
          )}
        </span>
        <span className="text-neutral-600">
          Next EIA: {data.next_eia_release}
        </span>
      </div>
      <div className="text-[9px] text-neutral-700 mt-0.5 italic">
        Informational context only — not a prediction.
      </div>
    </div>
  )
}

export default function EIAPredictionPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/analytics/eia-prediction`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setData(d)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  if (!data?.available && !loading) return null

  const pred = data?.current
  const changePct = pred?.tanker_change_pct

  return (
    <Panel
      id="houston-tanker-activity"
      title="HOUSTON TANKER ACTIVITY"
      info="Houston-zone tanker counts compared to a 30-day rolling average. Provides context on inbound import activity before the EIA weekly release. Informational only — not a forecast."
      collapsible
      headerRight={
        changePct != null && (
          <span className={`font-mono text-[10px] font-bold ${changeColor(changePct)}`}>
            {changePct >= 0 ? '+' : ''}{changePct}%
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading…
        </div>
      )}
      {!loading && pred && (
        <>
          {/* Headline metric */}
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-lg font-bold text-neutral-100">
                {pred.tanker_count}
              </span>
              <span className="font-mono text-[10px] text-neutral-500">
                Houston tankers · 7-day average
              </span>
            </div>
            <div className="font-mono text-[10px] text-neutral-500 mt-1">
              Next EIA release: {data.next_eia_release}
            </div>
          </div>

          {/* Stats */}
          <div className="px-4 py-3 border-b border-border/30 space-y-1.5">
            {pred.tanker_count_30d_avg != null && (
              <div className="flex items-center justify-between font-mono text-[10px]">
                <span className="text-neutral-500">30-day average</span>
                <span className="text-neutral-400">{pred.tanker_count_30d_avg}</span>
              </div>
            )}
            {changePct != null && (
              <div className="flex items-center justify-between font-mono text-[10px]">
                <span className="text-neutral-500">Change vs 30d</span>
                <span className={changeColor(changePct)}>
                  {changePct >= 0 ? '+' : ''}{changePct}%
                </span>
              </div>
            )}
            {pred.anchored_ratio != null && (
              <div className="flex items-center justify-between font-mono text-[10px]">
                <span className="text-neutral-500">Anchored ratio</span>
                <span className="text-neutral-400">{(pred.anchored_ratio * 100).toFixed(1)}%</span>
              </div>
            )}
            {pred.pearson_r != null && (
              <div className="flex items-center justify-between font-mono text-[10px]">
                <span className="text-neutral-500">Pearson r (Houston ↔ EIA, observed)</span>
                <span className="text-neutral-300">
                  {pred.pearson_r.toFixed(3)} @ {pred.optimal_lag_days}d lag
                </span>
              </div>
            )}
          </div>

          {/* History table — observed values only, no prediction column */}
          {data.history?.length > 0 && (
            <div className="max-h-[200px] overflow-y-auto scrollbar-hidden">
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="text-neutral-600 border-b border-border/30">
                    <th className="text-left px-3 py-1.5">DATE</th>
                    <th className="text-right px-2 py-1.5">HOUSTON Δ%</th>
                    <th className="text-right px-3 py-1.5">EIA ACTUAL</th>
                  </tr>
                </thead>
                <tbody>
                  {data.history.slice(0, 12).map((h, i) => (
                    <tr key={i} className="border-b border-border/10">
                      <td className="px-3 py-1 text-neutral-500">{h.date}</td>
                      <td className={`px-2 py-1 text-right ${changeColor(h.tanker_change_pct)}`}>
                        {h.tanker_change_pct != null
                          ? `${h.tanker_change_pct >= 0 ? '+' : ''}${h.tanker_change_pct}%`
                          : '—'}
                      </td>
                      <td className="px-3 py-1 text-right text-neutral-400">
                        {h.actual_change != null
                          ? `${h.actual_change > 0 ? '+' : ''}${(h.actual_change / 1000).toFixed(1)}M`
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="px-4 py-2 border-t border-border">
            <span className="font-mono text-[8px] text-neutral-700 italic">
              Houston tanker counts are aggregated from public AIS feeds. Past correlation does not imply predictive power. Not investment advice.
            </span>
          </div>
        </>
      )}
    </Panel>
  )
}
