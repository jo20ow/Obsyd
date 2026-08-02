import { fuelColor } from '../../utils/fuels'

// Vocabulary of the price-setting-technology fill, in ONE place because three
// modules need it and none of them may import each other: fills.js (zone
// color + tooltip lines), Legend.jsx (swatches — fills.js already imports it,
// so the reverse import would be a cycle) and tooltip.js.
//
// Backend: GET /api/power/marginal/overview (backend/power/marginal.py) —
// per zone the most expensive band that meaningfully dispatches in a FIXED
// merit order. Seven techs, closed set.

// tech -> canonical fuel of the CVD-validated palette (utils/fuels.js). The
// map introduces NO colour of its own: the fill re-uses exactly the hues the
// generation-mix charts already teach, so orange means gas on both surfaces.
// hydro_flex borrows Hydro Reservoir (the band it is attributed from, B12/B10)
// and must_run_renewables the Other Renewable green.
export const TECH_FUEL = {
  must_run_renewables: 'Other Renewable',
  nuclear: 'Nuclear',
  lignite: 'Lignite',
  hard_coal: 'Hard Coal',
  gas: 'Fossil Gas',
  oil: 'Oil',
  hydro_flex: 'Hydro Reservoir',
}

// fuels.js speaks CSS hex (Recharts), deck.gl wants [r, g, b] — and the repo
// has no hex→rgb helper. Converted ONCE per tech at module load, so getColor
// hands deck.gl the same array instance for every zone of a tech (no
// per-feature allocation, no per-feature parse).
const hexRgb = (hex) => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
]

const TECH_RGB = Object.fromEntries(
  Object.entries(TECH_FUEL).map(([tech, fuel]) => [tech, hexRgb(fuelColor(fuel))])
)

// null = tech outside the closed set (or absent) — the caller paints no-data
// rather than inventing a hue, exactly as fuels.js refuses to cycle colours.
export const techRgb = (tech) => TECH_RGB[tech] ?? null
export const techHex = (tech) => (TECH_FUEL[tech] ? fuelColor(TECH_FUEL[tech]) : null)

const EMPTY_INDEX = new Map()

// zone -> overview row, built ONCE per payload (the fill's getColor runs per
// map feature — scanning the 37-row array per zone would be quadratic). Keyed
// on the payload's object identity in a WeakMap: useFetchWithError hands out a
// stable object per fetch and the entry dies with it.
const indexCache = new WeakMap()

export function techIndex(extra) {
  if (!extra?.zones) return EMPTY_INDEX
  let idx = indexCache.get(extra)
  if (!idx) {
    idx = new Map(extra.zones.map((z) => [z.zone, z]))
    indexCache.set(extra, idx)
  }
  return idx
}
