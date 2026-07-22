// Canonical fuel palette — ONE source of truth for every generation-mix chart
// (GenerationMixPanel, GenMixHistoryPanel, MiniMixCard, CapturePanel).
//
// Before this file each chart kept its own map, two of them keyed to raw
// ENTSO-E names the APIs never emit — those fuels fell into an index cycle and
// three different fuels came out near-identical orange, differently per zone.
//
// Rules:
// - color follows the FUEL, never its index. No cycling: unknown fuels all get
//   the one gray, because a made-up hue is a made-up identity.
// - one hue per family, big lightness steps inside a family: gas=orange (the
//   only orange), coal-derived gas=red, coal/oil=browns and grays,
//   nuclear=violet, hydro=blues, wind=cyans, solar=yellow, biomass and other
//   renewables=greens, geothermal=rose, marine=teal.
// - validated with CVD simulation over adjacent pairs in STACK_ORDER against
//   both surfaces (#0f1115 dark, #ffffff light): worst adjacent ΔE 16.5
//   deutan / 10.7 tritan; every mix chart also names fuels in legend/tooltip,
//   so identity is never color-alone. Waste/Oil/Other are deliberately
//   recessive grays.

// Bottom of the stack = firm/dispatchable, top = variable renewables, so the
// stack reads the same in every zone and family neighbours keep their ΔE.
export const STACK_ORDER = [
  'Nuclear',
  'Lignite',
  'Hard Coal',
  'Fossil Coal-derived gas',
  'Fossil Gas',
  'Oil',
  'Oil Shale',
  'Peat',
  'Waste',
  'Other',
  'Biomass',
  'Geothermal',
  'Marine',
  'Hydro Reservoir',
  'Hydro Run-of-river',
  'Hydro Pumped Storage',
  'Other Renewable',
  'Wind Offshore',
  'Wind Onshore',
  'Solar',
]

const CANONICAL = {
  'Nuclear': '#a78bfa',
  'Lignite': '#b45309',
  'Hard Coal': '#a8a29e',
  'Fossil Coal-derived gas': '#dc2626',
  'Fossil Gas': '#fb923c',
  'Oil': '#64748b',
  'Oil Shale': '#78716c',
  'Peat': '#ca8a04',
  'Waste': '#a1a1aa',
  'Other': '#6b7280',
  'Biomass': '#4ade80',
  'Geothermal': '#f43f5e',
  'Marine': '#2dd4bf',
  'Hydro Reservoir': '#2563eb',
  'Hydro Run-of-river': '#60a5fa',
  'Hydro Pumped Storage': '#6366f1',
  'Other Renewable': '#16a34a',
  'Wind Offshore': '#0891b2',
  'Wind Onshore': '#67e8f9',
  'Solar': '#facc15',
}

// The APIs emit PSR_LABELS (backend/power/entsoe_grid.py); CapturePanel keys by
// raw B-code; old rows / other feeds may carry ENTSO-E's long names. All three
// spellings resolve to the same canonical fuel.
const ALIASES = {
  B01: 'Biomass',
  B02: 'Lignite',
  B03: 'Fossil Coal-derived gas',
  B04: 'Fossil Gas',
  B05: 'Hard Coal',
  B06: 'Oil',
  B07: 'Oil Shale',
  B08: 'Peat',
  B09: 'Geothermal',
  B10: 'Hydro Pumped Storage',
  B11: 'Hydro Run-of-river',
  B12: 'Hydro Reservoir',
  B13: 'Marine',
  B14: 'Nuclear',
  B15: 'Other Renewable',
  B16: 'Solar',
  B17: 'Waste',
  B18: 'Wind Offshore',
  B19: 'Wind Onshore',
  B20: 'Other',
  'Fossil Brown coal/Lignite': 'Lignite',
  'Fossil Hard coal': 'Hard Coal',
  'Fossil Oil': 'Oil',
  'Fossil Oil shale': 'Oil Shale',
  'Fossil Peat': 'Peat',
  'Hydro Water Reservoir': 'Hydro Reservoir',
  'Hydro Run-of-river and poundage': 'Hydro Run-of-river',
  'Other renewable': 'Other Renewable',
}

export const DEFAULT_FUEL_COLOR = '#6b7280'

export function fuelColor(key) {
  return CANONICAL[key] ?? CANONICAL[ALIASES[key]] ?? DEFAULT_FUEL_COLOR
}

// Friendly display name for a fuel key. Most panels (GenerationMixPanel, CapturePanel)
// receive an already-canonical label from the backend and never need this — but
// backend/power/live.py deliberately ships the RAW ENTSO-E code (`gen.<Bxx>` -> "B16")
// and defers the mapping here, so a raw code is resolved through ALIASES; an
// already-canonical name (or anything unknown) passes through unchanged.
export function fuelLabel(key) {
  return CANONICAL[key] ? key : (ALIASES[key] ?? key)
}

const ORDER_INDEX = new Map(STACK_ORDER.map((label, i) => [label, i]))

function orderIndex(key) {
  return ORDER_INDEX.get(key) ?? ORDER_INDEX.get(ALIASES[key]) ?? STACK_ORDER.length
}

// Stable stack order for any fuel list the API returns (it sends them
// alphabetically, which puts look-alike families side by side).
export function sortFuels(fuels) {
  return [...fuels].sort((a, b) => orderIndex(a) - orderIndex(b) || a.localeCompare(b))
}
