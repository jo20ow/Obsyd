// The Honest-Record charter series, shared by the EXPLORE tab's quality panels.
// Mirrors backend/power/quality.py::QUALITY_SERIES (+ ZONE_SERIES_KEY) — a
// stable config tuple, so a mirrored list beats a fetch (fuels.js precedent:
// one module is the single frontend home for these keys and their labels).

// The reserved zone-level pseudo-series: cross-series rule flags only —
// completeness / revisions / lag are null by contract, and it has no store
// series of its own, so the revision ledger never carries it.
export const ZONE_SERIES_KEY = '_zone'

// Ordered as the backend tuple orders them (registry order, deterministic).
export const QUALITY_SERIES = [
  { key: 'load.actual', label: 'Load (actual)' },
  { key: 'price.dayahead', label: 'Day-ahead price' },
  { key: 'price.dayahead.qh', label: 'Day-ahead price · 15-min' },
  { key: 'gen.B16', label: 'Solar generation' },
  { key: 'gen.B18', label: 'Wind offshore generation' },
  { key: 'gen.B19', label: 'Wind onshore generation' },
]

const LABELS = {
  ...Object.fromEntries(QUALITY_SERIES.map((s) => [s.key, s.label])),
  [ZONE_SERIES_KEY]: 'zone-level checks',
}

export const qualitySeriesLabel = (key) => LABELS[key] || key
