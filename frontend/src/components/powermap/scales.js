export function lerp(a, b, t) {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ]
}

// v → RGB for the diverging price scale on the fixed domain [lo, hi] (lo may be ≥0,
// then only the positive pole is in play). Values are clamped to the domain.
export function priceColor(v, lo, hi, pal) {
  if (v == null) return pal.mid
  if (v >= 0) {
    const span = Math.max(hi, 1e-6)
    return lerp(pal.mid, pal.posPole, Math.min(Math.max(v / span, 0), 1))
  }
  const span = Math.max(-lo, 1e-6)
  return lerp(pal.mid, pal.negPole, Math.min(Math.max(-v / span, 0), 1))
}

// The legend is GENERATED from priceColor — it cannot drift from the map.
export function legendGradient(lo, hi, pal) {
  const stops = []
  for (let i = 0; i <= 12; i++) {
    const v = lo + ((hi - lo) * i) / 12
    const [r, g, b] = priceColor(v, lo, hi, pal)
    stops.push(`rgb(${r},${g},${b}) ${(i / 12) * 100}%`)
  }
  return `linear-gradient(90deg, ${stops.join(', ')})`
}

export function percentile(sorted, p) {
  if (sorted.length === 0) return 0
  const i = (sorted.length - 1) * p
  const f = Math.floor(i)
  return sorted[f] + (sorted[Math.min(f + 1, sorted.length - 1)] - sorted[f]) * (i - f)
}

// All price values in the shown window — every zone × every snapshot hour,
// plus the live overview rows. This is the population behind the fixed weekly
// color domain (and the population a quantile scale would classify).
export function collectWeekValues(snap, rows) {
  const vals = []
  if (snap?.zones) {
    for (const col of Object.values(snap.zones)) {
      for (const v of col) if (v != null) vals.push(v)
    }
  }
  for (const z of rows || []) if (z.price_close != null) vals.push(z.price_close)
  return vals
}

// FIXED color domain over the whole 7-day window (all zones × all hours), so
// scrubbing compares hours honestly — a per-frame min/max would repaint every
// zone each step and make yesterday incomparable to today. p2/p98 clamp keeps
// one spike hour from crushing the rest of the scale; the legend says so.
// Carries the sorted `vals` population so scale construction stays a
// scales.js-only concern.
export function weekDomain(snap, rows) {
  const vals = collectWeekValues(snap, rows)
  if (!vals.length) return { lo: 0, hi: 1, vals }
  vals.sort((a, b) => a - b)
  const p2 = percentile(vals, 0.02)
  const p95 = percentile(vals, 0.95)
  return { lo: Math.min(p2, 0), hi: Math.max(p95, 1), vals }
}
