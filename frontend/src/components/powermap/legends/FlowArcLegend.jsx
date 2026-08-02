import { rgbCss } from '../palettes'
import { UTIL_MID, UTIL_HIGH } from '../constants'

/* Flow-arc legend — swatches read straight from pal.arc (they cannot
   drift from the layer) and thresholds from UTIL_MID/UTIL_HIGH. Only the
   ■ carries the series color; the label text stays neutral ink (amber-600
   text at 9px would sit at ~2.9:1 on the light surface). Only shown while
   the arcs themselves are on (index.jsx couples it to overlays.flows). */
export default function FlowArcLegend({ pal, atLatest }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-4 py-1.5 border-t border-border font-mono text-[9px] text-neutral-600">
      <span>flows: width ∝ GW (√, caps at 5)</span>
      <span><span style={{ color: rgbCss(pal.arc.low) }}>■</span> &lt;{UTIL_MID}%</span>
      <span><span style={{ color: rgbCss(pal.arc.mid) }}>■</span> {UTIL_MID}–{UTIL_HIGH}%</span>
      <span><span style={{ color: rgbCss(pal.arc.high) }}>■</span> ≥{UTIL_HIGH}% of NTC</span>
      <span><span style={{ color: rgbCss(pal.arc.proxy) }}>■</span> no NTC (p95)</span>
      <span><span style={{ color: rgbCss(pal.arc.none) }}>■</span> no reading</span>
      <span>grey = context (thin)</span>
      <span>solid end = importer</span>
      {!atLatest && <span className="text-neutral-500">hidden while scrubbing — arcs show latest only</span>}
    </div>
  )
}
