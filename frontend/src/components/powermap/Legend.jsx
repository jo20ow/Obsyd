import { rgbCss } from './palettes'
import { UTIL_MID, UTIL_HIGH } from './constants'
import { legendGradient } from './scales'

/* Flow-arc legend — swatches read straight from pal.arc (they cannot
   drift from the layer) and thresholds from UTIL_MID/UTIL_HIGH. Only the
   ■ carries the series color; the label text stays neutral ink (amber-600
   text at 9px would sit at ~2.9:1 on the light surface). Only shown while
   the arcs themselves are on (index.jsx couples it to overlays.flows). */
export function FlowArcLegend({ pal, atLatest }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-4 py-1.5 border-t border-border font-mono text-[9px] text-neutral-600">
      <span>flows: width ∝ GW (√, caps at 5)</span>
      <span><span style={{ color: rgbCss(pal.arc.low) }}>■</span> &lt;{UTIL_MID}%</span>
      <span><span style={{ color: rgbCss(pal.arc.mid) }}>■</span> {UTIL_MID}–{UTIL_HIGH}%</span>
      <span><span style={{ color: rgbCss(pal.arc.high) }}>■</span> ≥{UTIL_HIGH}% of NTC</span>
      <span><span style={{ color: rgbCss(pal.arc.proxy) }}>■</span> no NTC (p95)</span>
      <span><span style={{ color: rgbCss(pal.arc.none) }}>■</span> no reading</span>
      <span>solid end = importer</span>
      {!atLatest && <span className="text-neutral-500">hidden while scrubbing — arcs show latest only</span>}
    </div>
  )
}

// Price gradient row: the fixed weekly p2/p98 domain, generated FROM priceColor
// via legendGradient — it cannot drift from the map.
export function PriceScaleLegend({ lo, hi, pal }) {
  return (
    <span className="flex items-center gap-1" title="Fixed scale across the shown week (2nd–98th percentile); the tooltip has exact values.">
      <span className="text-neutral-500">{lo < 0 ? `≤${lo.toFixed(0)}` : lo.toFixed(0)}</span>
      <span className="relative inline-block h-2 w-28 rounded overflow-hidden" style={{ background: legendGradient(lo, hi, pal) }}>
        {lo < 0 && (
          <span
            className="absolute top-0 h-2 w-px bg-neutral-400"
            style={{ left: `${((0 - lo) / (hi - lo)) * 100}%` }}
            title="0 €/MWh"
          />
        )}
      </span>
      <span className="text-neutral-500">≥{hi.toFixed(0)} €/MWh</span>
      {lo < 0 && <span className="ml-1 text-violet-300/70">violet = negative</span>}
    </span>
  )
}

// Grid-state trio for the state fill.
export function StateLegend({ pal }) {
  return (
    <span className="flex items-center gap-3">
      <span style={{ color: pal.stateLegend.CALM }}>■ CALM</span>
      <span style={{ color: pal.stateLegend.ELEVATED }}>■ ELEVATED</span>
      <span style={{ color: pal.stateLegend.STRESSED }}>■ STRESSED</span>
    </span>
  )
}
