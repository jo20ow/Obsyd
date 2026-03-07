import { useState, useEffect } from 'react'
import Panel from './Panel'

const API = '/api'

const STRUCTURE_STYLES = {
  backwardation: { text: 'text-red-400', label: 'BACKWARDATION', hint: 'tight supply' },
  contango: { text: 'text-green-glow', label: 'CONTANGO', hint: 'storage profitable' },
  flat: { text: 'text-neutral-500', label: 'FLAT', hint: 'neutral' },
}

function CurveRow({ name, curve }) {
  if (!curve) return null
  const style = STRUCTURE_STYLES[curve.structure] || STRUCTURE_STYLES.flat
  const spreadSign = (curve.spread ?? 0) >= 0 ? '+' : ''

  return (
    <div className="flex items-center justify-between py-1.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-neutral-500 w-12">{name}</span>
        <span className="font-mono text-xs text-neutral-300">
          ${(curve.front_month ?? 0).toFixed(2)}
        </span>
        <span className="font-mono text-[9px] text-neutral-600">→</span>
        <span className="font-mono text-xs text-neutral-400">
          ${(curve.next_month ?? 0).toFixed(2)}
        </span>
      </div>
      <div className="text-right">
        <span className={`font-mono text-xs font-bold ${style.text}`}>
          {spreadSign}{(curve.spread_pct ?? 0).toFixed(1)}%
        </span>
      </div>
    </div>
  )
}

export default function MarketStructure() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API}/signals/market-structure`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch((e) => console.error('MarketStructure fetch:', e))
  }, [])

  if (!data || !data.curves || Object.keys(data.curves).length === 0) return null

  const summaryStyle = STRUCTURE_STYLES[data.summary] || STRUCTURE_STYLES.flat

  return (
    <Panel id="market-structure" title="FUTURES CURVE" info="Contango = futures > spot (oversupply). Backwardation = spot > futures (tight supply)." headerRight={<span className={`font-mono text-[10px] font-bold ${summaryStyle.text}`}>{summaryStyle.label}</span>}>
      <div className="px-4 py-1">
        {Object.entries(data.curves).map(([name, curve]) => (
          <CurveRow key={name} name={name} curve={curve} />
        ))}
      </div>
      <div className="px-4 py-1.5 border-t border-border/50 font-mono text-[8px] text-neutral-700">
        Front vs next month // {summaryStyle.hint}
      </div>
    </Panel>
  )
}
