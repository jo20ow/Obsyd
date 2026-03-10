import { useState, useEffect } from 'react'
import Panel from './Panel'

const API = '/api'

const PRED_STYLES = {
  BUILD: { color: 'text-red-400', bg: 'bg-red-400/10', label: 'BUILD likely', hint: 'bearish' },
  DRAW: { color: 'text-green-glow', bg: 'bg-green-glow/10', label: 'DRAW likely', hint: 'bullish' },
  NEUTRAL: { color: 'text-neutral-400', bg: 'bg-neutral-400/10', label: 'NEUTRAL', hint: 'mixed signals' },
}

export function EIAPredictionMini() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API}/analytics/eia-prediction`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {})
  }, [])

  if (!data?.available) return null

  const pred = data.current
  const style = PRED_STYLES[pred.prediction] || PRED_STYLES.NEUTRAL
  const accuracy = data.accuracy

  return (
    <div className={`font-mono text-[10px] ${style.bg} border border-border/30 rounded px-3 py-2 mt-1`}>
      <div className="flex items-center justify-between">
        <span className="text-neutral-500">
          AIS Signal: <span className={`font-bold ${style.color}`}>{style.label}</span>
          {pred.tanker_change_pct != null && (
            <span className="text-neutral-600 ml-1">
              (Houston tankers {pred.tanker_change_pct >= 0 ? '+' : ''}{pred.tanker_change_pct}% vs 30d avg)
            </span>
          )}
        </span>
        {accuracy.sufficient_data && (
          <span className="text-neutral-600">
            Accuracy: {accuracy.hit_rate}% (n={accuracy.total_predictions})
          </span>
        )}
      </div>
      <div className="text-[9px] text-neutral-700 mt-0.5 italic">
        Experimental indicator based on AIS vessel counts. Limited historical validation.
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
  const style = pred ? (PRED_STYLES[pred.prediction] || PRED_STYLES.NEUTRAL) : PRED_STYLES.NEUTRAL
  const accuracy = data?.accuracy

  return (
    <Panel
      id="eia-prediction"
      title="EIA INVENTORY PREDICTION"
      info="Uses Houston zone AIS tanker activity to predict US crude inventory builds/draws. More tankers arriving = higher imports = BUILD likely. Experimental — not investment advice."
      collapsible
      headerRight={
        pred && (
          <span className={`font-mono text-[10px] font-bold ${style.color}`}>
            {style.label}
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading prediction...
        </div>
      )}
      {!loading && pred && (
        <>
          {/* Prediction */}
          <div className={`px-4 py-3 border-b border-border/30 ${style.bg}`}>
            <div className="flex items-center gap-2">
              <span className={`font-mono text-lg font-bold ${style.color}`}>
                {pred.prediction}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">({style.hint})</span>
            </div>
            <div className="font-mono text-[10px] text-neutral-500 mt-1">
              Next EIA: {data.next_eia_release}
            </div>
          </div>

          {/* Stats */}
          <div className="px-4 py-3 border-b border-border/30 space-y-1.5">
            <div className="flex items-center justify-between font-mono text-[10px]">
              <span className="text-neutral-500">Houston tankers (7d avg)</span>
              <span className="text-neutral-300">{pred.tanker_count}</span>
            </div>
            {pred.tanker_count_30d_avg != null && (
              <div className="flex items-center justify-between font-mono text-[10px]">
                <span className="text-neutral-500">30d average</span>
                <span className="text-neutral-400">{pred.tanker_count_30d_avg}</span>
              </div>
            )}
            {pred.tanker_change_pct != null && (
              <div className="flex items-center justify-between font-mono text-[10px]">
                <span className="text-neutral-500">Change vs 30d</span>
                <span className={pred.tanker_change_pct >= 0 ? 'text-red-400' : 'text-green-glow'}>
                  {pred.tanker_change_pct >= 0 ? '+' : ''}{pred.tanker_change_pct}%
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
                <span className="text-neutral-500">Pearson r (Houston → EIA)</span>
                <span className="text-neutral-300">
                  {pred.pearson_r.toFixed(3)} @ {pred.optimal_lag_days}d lag
                </span>
              </div>
            )}
          </div>

          {/* Accuracy */}
          {accuracy && (
            <div className="px-4 py-3 border-b border-border/30">
              <div className="flex items-center justify-between font-mono text-[10px]">
                <span className="text-neutral-500">Historical accuracy</span>
                {accuracy.sufficient_data ? (
                  <span className="text-neutral-300">
                    {accuracy.hit_rate}% ({accuracy.correct_predictions}/{accuracy.total_predictions})
                  </span>
                ) : (
                  <span className="text-neutral-600 italic">
                    Insufficient data ({accuracy.total_predictions}/8 required)
                  </span>
                )}
              </div>
            </div>
          )}

          {/* History table */}
          {data.history?.length > 0 && (
            <div className="max-h-[200px] overflow-y-auto scrollbar-hidden">
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="text-neutral-600 border-b border-border/30">
                    <th className="text-left px-3 py-1.5">DATE</th>
                    <th className="text-left px-2 py-1.5">PRED</th>
                    <th className="text-right px-2 py-1.5">ACTUAL</th>
                    <th className="text-right px-3 py-1.5">RESULT</th>
                  </tr>
                </thead>
                <tbody>
                  {data.history.slice(0, 12).map((h, i) => (
                    <tr key={i} className="border-b border-border/10">
                      <td className="px-3 py-1 text-neutral-500">{h.date}</td>
                      <td className="px-2 py-1">
                        <span className={(PRED_STYLES[h.prediction] || PRED_STYLES.NEUTRAL).color}>
                          {h.prediction}
                        </span>
                      </td>
                      <td className="px-2 py-1 text-right text-neutral-400">
                        {h.actual_change != null ? `${h.actual_change > 0 ? '+' : ''}${(h.actual_change / 1000).toFixed(1)}M` : '—'}
                      </td>
                      <td className="px-3 py-1 text-right">
                        {h.correct === 1 && <span className="text-green-glow">✓</span>}
                        {h.correct === 0 && <span className="text-red-400">✗</span>}
                        {h.correct == null && <span className="text-neutral-700">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="px-4 py-2 border-t border-border">
            <span className="font-mono text-[8px] text-neutral-700 italic">
              Experimental indicator based on AIS vessel counts. Limited historical validation. Not investment advice.
            </span>
          </div>
        </>
      )}
    </Panel>
  )
}
