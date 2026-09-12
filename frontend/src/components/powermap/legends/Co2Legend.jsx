import { CO2_MAX_G, CO2_MID_G, co2Color } from '../scales'
import { rgbCss } from '../palettes'

// CO₂-intensity fill legend. The bar is an ABSOLUTE g/kWh axis (unlike the
// price legend's CDF bar): 0 → 650+, sampled through co2Color itself so the
// legend cannot drift from the map. Values above the dirty anchor clamp — the
// ratio cannot exceed its largest factor, so the right edge honestly reads
// "650+". A dead feed is reported here (the fills contract: legends never
// fail silently), because a map with every zone in the no-data slate needs a
// worded reason next to it.
const TICKS = [0, 150, CO2_MID_G, 450, CO2_MAX_G]

export default function Co2Legend({ pal, extra, extraError }) {
  const gradient = `linear-gradient(90deg, ${Array.from({ length: 14 }, (_, i) => {
    const t = i / 13
    return `${rgbCss(co2Color(t * CO2_MAX_G, pal))} ${(t * 100).toFixed(1)}%`
  }).join(', ')})`
  return (
    <span
      className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5"
      title="Estimated carbon intensity of each zone's own generation: published mix × per-technology factors (IPCC AR5 lifecycle medians via Electricity Maps). Absolute scale — 50 is clean in every zone, 650 is coal in every zone. Tooltips carry the exact value and the hour it was computed for."
    >
      <span className="text-neutral-500">0</span>
      <span className="flex flex-col gap-px">
        <span className="block h-2 w-40 rounded overflow-hidden" style={{ background: gradient }} />
        <span className="relative block h-2.5 w-40 text-[8px] leading-none text-neutral-500">
          {TICKS.slice(1, -1).map((g) => (
            <span key={g} className="absolute -translate-x-1/2" style={{ left: `${(g / CO2_MAX_G) * 100}%` }}>
              {g}
            </span>
          ))}
        </span>
      </span>
      <span className="text-neutral-500">{CO2_MAX_G}+ g/kWh</span>
      <span className="text-neutral-600">absolute scale · lifecycle · estimated</span>
      {extraError && <span className="text-amber-500">CO₂ feed unavailable — zones show no-data</span>}
      {!extraError && !extra && <span className="text-neutral-600">loading…</span>}
    </span>
  )
}
