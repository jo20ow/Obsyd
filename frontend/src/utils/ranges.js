// Canonical date-range vocabulary shared by the global range selector and every
// panel. Before this, five panels each defined their own RANGES array + a copy of
// isoDaysAgo, and the POWER panels hardcoded `days=` — three incompatible dialects.
// This is the single source so one control can drive the whole desk.

export const RANGES = [
  { key: '7d', label: '7D', days: 7 },
  { key: '30d', label: '30D', days: 30 },
  { key: '90d', label: '90D', days: 90 },
  { key: '1y', label: '1Y', days: 365 },
  { key: '5y', label: '5Y', days: 1826 },
]

export const RANGE_KEYS = RANGES.map((r) => r.key)
export const DEFAULT_RANGE = '30d'

export function rangeDays(key) {
  return RANGES.find((r) => r.key === key)?.days ?? 30
}

// YYYY-MM-DD `n` days before today (UTC) — kept out of render to avoid Date.now churn.
export function isoDaysAgo(n) {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - n)
  return d.toISOString().slice(0, 10)
}

// `start=` for /api/v1/* endpoints, from a range key. `minDays` floors the window
// for panels that need a minimum history to be meaningful (e.g. monthly aggregates
// need >= 1y), so a short global range still renders something useful there.
export function rangeStart(key, minDays = 0) {
  return isoDaysAgo(Math.max(rangeDays(key), minDays))
}
