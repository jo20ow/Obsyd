import { rgbCss } from './palettes'
import { UTIL_MID, UTIL_HIGH } from './constants'

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

// Price scale row. The bar is the week's CDF axis (left = cheapest observed
// hour, right = most expensive): equal bar length = equal share of the week's
// all-zone hours — that IS the equal-frequency contract. Every stop and tick
// comes from the scale object itself, so the legend cannot drift from the map.
// Ticks under the bar name the p10/p25/p50/p75/p90 prices; endpoints are the
// observed min/max (no clamp — ranks are outlier-robust by construction). The
// zero marker sits at 0 €'s share of the population.
export function PriceScaleLegend({ scale }) {
  const stops = scale.stops(12)
  const gradient = `linear-gradient(90deg, ${stops
    .map((c, i) => `${rgbCss(c)} ${(i / (stops.length - 1)) * 100}%`)
    .join(', ')})`
  const q = scale.quantiles
  const ticks = q ? [[10, q.p10], [25, q.p25], [50, q.p50], [75, q.p75], [90, q.p90]] : []
  return (
    <span
      className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5"
      title="Fixed equal-frequency scale across the shown week: colors spread by rank among all zones × hours, not by € distance — tooltips carry exact €. Ticks mark the p10/p25/median/p75/p90 prices."
    >
      <span className="text-neutral-500">{scale.lo.toFixed(0)}</span>
      <span className="flex flex-col gap-px">
        <span className="relative block h-2 w-36 rounded overflow-hidden" style={{ background: gradient }}>
          {scale.hasNegatives && (
            <span
              className="absolute top-0 h-2 w-px bg-neutral-400"
              style={{ left: `${scale.negShare * 100}%` }}
              title="0 €/MWh"
            />
          )}
        </span>
        {ticks.length > 0 && (
          <span className="relative block h-2.5 w-36 text-[8px] leading-none text-neutral-500">
            {ticks.map(([p, v]) => (
              <span key={p} className="absolute -translate-x-1/2" style={{ left: `${p}%` }} title={`p${p}`}>
                {v.toFixed(0)}
              </span>
            ))}
          </span>
        )}
      </span>
      <span className="text-neutral-500">{scale.hi.toFixed(0)} €/MWh</span>
      <span className="text-neutral-600">equal-frequency scale · week&apos;s all-zone hours</span>
      {scale.hasNegatives && <span className="text-violet-300/70">violet = negative</span>}
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
