import { DEFAULT_FUEL_COLOR, fuelColor } from '../../utils/fuels'

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
// Null-prototype on purpose: the keys are payload strings, so a zone shipped
// as tech "constructor" or "toString" must read as UNKNOWN, never inherit a
// Function that getColor would then try to spread into an RGB.
export const TECH_FUEL = Object.freeze(Object.assign(Object.create(null), {
  must_run_renewables: 'Other Renewable',
  nuclear: 'Nuclear',
  lignite: 'Lignite',
  hard_coal: 'Hard Coal',
  gas: 'Fossil Gas',
  oil: 'Oil',
  hydro_flex: 'Hydro Reservoir',
}))

// fuels.js speaks CSS hex (Recharts), deck.gl wants [r, g, b] — and the repo
// has no hex→rgb helper. Converted ONCE per tech at module load, so getColor
// hands deck.gl the same array instance for every zone of a tech (no
// per-feature allocation, no per-feature parse).
const hexRgb = (hex) => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
]

const TECH_RGB = new Map(
  Object.entries(TECH_FUEL).map(([tech, fuel]) => [tech, hexRgb(fuelColor(fuel))])
)

// null = tech outside the closed set (or absent) — the caller paints no-data
// rather than inventing a hue, exactly as fuels.js refuses to cycle colours.
export const techRgb = (tech) => TECH_RGB.get(tech) ?? null
export const techHex = (tech) =>
  (Object.hasOwn(TECH_FUEL, tech) ? fuelColor(TECH_FUEL[tech]) : null)

// The claim "the legend cannot drift from the map" only holds while every
// mapping still RESOLVES in fuels.js. A rename there would quietly hand two
// techs the same fallback gray, and the map would keep drawing confidently —
// so say it out loud in dev instead. (Dev-only: no cost in the bundle's
// production branch, and the seven names are a compile-time-ish constant.)
if (import.meta.env.DEV) { // statically replaced by Vite — the block leaves the prod bundle entirely
  const unresolved = Object.entries(TECH_FUEL).filter(([, fuel]) => fuelColor(fuel) === DEFAULT_FUEL_COLOR)
  const distinct = new Set([...TECH_RGB.values()].map((rgb) => rgb.join(',')))
  if (unresolved.length) {
    console.error('powermap/tech: these techs no longer resolve in utils/fuels.js (they would all paint the fallback gray):', unresolved)
  } else if (distinct.size !== TECH_RGB.size) {
    console.error('powermap/tech: two technologies now share one colour — the map and its legend cannot stay honest:', [...TECH_RGB])
  }
}

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
