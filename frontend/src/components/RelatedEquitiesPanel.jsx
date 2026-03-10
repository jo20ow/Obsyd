import { useState, useEffect } from 'react'
import Panel from './Panel'

const API = '/api'

const INFO_TEXT =
  'Related energy equities grouped by sector. ' +
  'Tanker stocks reflect tanker freight demand. ' +
  'LNG shipping stocks reflect global gas trade flows. ' +
  'These are observations, not investment recommendations.'

export default function RelatedEquitiesPanel() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API}/prices/equities`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {})
  }, [])

  if (!data) return null

  const tanker = data.tanker || []
  const lng = data.lng || []
  if (tanker.length === 0 && lng.length === 0) return null

  return (
    <Panel id="related-equities" title="RELATED EQUITIES" info={INFO_TEXT} collapsible>
      <div className="px-4 py-3 font-mono text-xs space-y-3">
        {tanker.length > 0 && (
          <div>
            <div className="text-[10px] text-neutral-600 tracking-wider mb-1.5">TANKER</div>
            <div className="space-y-1">
              {tanker.map((eq) => (
                <div key={eq.ticker} className="flex items-center justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-cyan-glow font-bold w-10 shrink-0">{eq.ticker}</span>
                    <span className="text-neutral-500 truncate">{eq.name}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-neutral-300">${eq.price}</span>
                    <span className={`w-16 text-right font-semibold ${eq.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {eq.change_pct >= 0 ? '+' : ''}{eq.change_pct}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {lng.length > 0 && (
          <div>
            <div className="text-[10px] text-neutral-600 tracking-wider mb-1.5">LNG SHIPPING</div>
            <div className="space-y-1">
              {lng.map((eq) => (
                <div key={eq.ticker} className="flex items-center justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-amber-400 font-bold w-10 shrink-0">{eq.ticker}</span>
                    <span className="text-neutral-500 truncate">{eq.name}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-neutral-300">${eq.price}</span>
                    <span className={`w-16 text-right font-semibold ${eq.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {eq.change_pct >= 0 ? '+' : ''}{eq.change_pct}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="text-[9px] text-neutral-700 border-t border-border pt-2">
          Correlation, not causation. Not investment advice.
        </div>
      </div>
    </Panel>
  )
}
