import { useState, useEffect } from 'react'
import Panel from './Panel'

const API = '/api'

const INFO_TEXT =
  'The 3:2:1 crack spread measures refinery profitability. ' +
  '3 barrels of crude produce 2 barrels of gasoline (RBOB) and 1 barrel of diesel (Heating Oil). ' +
  'High spread = strong refining margins = bullish crude demand. ' +
  'Low spread = weak margins = potential crude demand destruction.'

export default function CrackSpreadPanel() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API}/signals/crack-spread`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {})
  }, [])

  if (!data || data.error) return null

  const spread = data.spread_321
  const avg30 = data.avg_30d
  const pctVs30 = avg30 ? (((spread - avg30) / avg30) * 100).toFixed(1) : null
  const pctColor = pctVs30 && parseFloat(pctVs30) >= 0 ? 'text-emerald-400' : 'text-red-400'

  return (
    <Panel id="crack-spread" title="3:2:1 CRACK SPREAD" info={INFO_TEXT} collapsible>
      <div className="px-4 py-3 font-mono text-xs space-y-3">
        {/* Main spread */}
        <div className="flex items-end justify-between">
          <div>
            <span className="text-2xl font-bold text-cyan-glow">
              ${spread}
            </span>
            <span className="text-neutral-500 ml-1">/bbl</span>
          </div>
          {pctVs30 && (
            <span className={`text-sm font-semibold ${pctColor}`}>
              {parseFloat(pctVs30) >= 0 ? '+' : ''}{pctVs30}%
            </span>
          )}
        </div>

        {avg30 && (
          <div className="text-neutral-500 text-[10px]">
            vs 30d avg: ${avg30}
          </div>
        )}

        {/* Components */}
        <div className="border-t border-border pt-2 space-y-1">
          <div className="flex justify-between">
            <span className="text-neutral-500">WTI</span>
            <span className="text-neutral-300">${data.wti} /bbl</span>
          </div>
          <div className="flex justify-between">
            <span className="text-neutral-500">RBOB</span>
            <span className="text-neutral-300">
              ${data.rbob} /gal
              <span className="text-neutral-600 ml-1">(${data.rbob_barrel} /bbl)</span>
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-neutral-500">HO</span>
            <span className="text-neutral-300">
              ${data.ho} /gal
              <span className="text-neutral-600 ml-1">(${data.ho_barrel} /bbl)</span>
            </span>
          </div>
        </div>

        {/* Percentile */}
        {data.percentile_1y != null && (
          <div className="text-[10px] text-neutral-600 border-t border-border pt-2">
            {data.percentile_1y}th percentile vs 1Y range
          </div>
        )}
      </div>
    </Panel>
  )
}
