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
