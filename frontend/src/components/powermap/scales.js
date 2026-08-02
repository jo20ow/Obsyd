function lerp(a, b, t) {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ]
}

function percentile(sorted, p) {
  if (sorted.length === 0) return 0
  const i = (sorted.length - 1) * p
  const f = Math.floor(i)
  return sorted[f] + (sorted[Math.min(f + 1, sorted.length - 1)] - sorted[f]) * (i - f)
}

// Interpolated empirical CDF: where does v sit among the sorted observed
// values, as a fraction in [0, 1]? Binary search + linear interpolation
// between neighbours (the inverse of `percentile`) — smooth, monotone, and
// clamped for values outside the observed range. Runs of duplicates are fine:
// the search lands past the run, so the interpolation denominator stays > 0.
function cdfRank(sorted, v) {
  const n = sorted.length
  if (n === 0 || v <= sorted[0]) return 0
  if (v >= sorted[n - 1]) return 1
  let lo = 0
  let hi = n - 1
  while (lo < hi) {
    const m = (lo + hi) >> 1
    if (sorted[m] > v) hi = m
    else lo = m + 1
  }
  // sorted[lo-1] <= v < sorted[lo]
  const frac = (v - sorted[lo - 1]) / (sorted[lo] - sorted[lo - 1])
  return (lo - 1 + frac) / (n - 1)
}

// All price values in the shown window — every zone × every snapshot hour,
// plus the live overview rows. This is the population the quantile scale
// classifies against.
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

// Equal-frequency (quantile) color scale over the week population.
//
// WHY not linear: in expensive evening hours nearly every zone sits in the top
// tenth of a week-fixed linear domain and the continent flattens into one blue.
// Ranks spread the colors by how a price sits WITHIN the week's distribution,
// so differentiation survives by construction (and outliers can't crush the
// scale — no p2/p98 clamp needed); the tooltip keeps the exact €.
//
// The domain is FIXED across the whole window: the scale is built ONCE from
// the full week population (all zones × all hours), so scrubbing repaints
// every hour against the SAME mapping — 20:00 yesterday stays honestly
// comparable to 20:00 today. Never rebuild it per frame.
//
// Negatives keep the violet pole and rank only among THEMSELVES (most-negative
// → full violet, closest-to-zero → ~mid): a negative price is a distinct
// market state and must never blend into "cheap".
export function makeQuantileScale(vals, pal) {
  const sorted = [...vals].sort((a, b) => a - b)
  const n = sorted.length
  let split = 0 // size of the negative slice
  while (split < n && sorted[split] < 0) split++
  const neg = sorted.slice(0, split)
  const nonNeg = sorted.slice(split)
  const negShare = n ? split / n : 0

  const color = (v) => {
    if (v == null || n === 0) return pal.mid
    if (v < 0) {
      // A negative outside the observed range — or with no negatives in the
      // population at all — clamps to FULL violet: it is more extreme than
      // anything seen, never "cheap".
      return lerp(pal.mid, pal.negPole, neg.length ? 1 - cdfRank(neg, v) : 1)
    }
    return lerp(pal.mid, pal.posPole, nonNeg.length ? cdfRank(nonNeg, v) : 0)
  }

  return {
    color,
    // Legend stops, POSITIONED on the CDF axis: [{pos, rgb}] with pos in
    // [0, 1]. The two sides of 0 are sampled SEPARATELY, with a doubled stop
    // at pos = negShare (violet side, then cyan side) pinning the handoff as
    // a hard edge — uniform sampling would smooth the violet→mid pinch away
    // whenever negatives are sparse (negShare < 1/nStops, the common live
    // case) and let prices just above zero render violet-ish. Every stop
    // still goes through color(), so the legend cannot drift from the map.
    stops(nStops) {
      if (n === 0) return [{ pos: 0, rgb: pal.mid }, { pos: 1, rgb: pal.mid }]
      const out = []
      if (neg.length) {
        const k = Math.max(2, Math.round(nStops * negShare))
        for (let i = 0; i <= k; i++) {
          out.push({ pos: (i / k) * negShare, rgb: color(percentile(neg, i / k)) })
        }
      }
      if (nonNeg.length) {
        const k = Math.max(2, nStops - Math.round(nStops * negShare))
        for (let i = 0; i <= k; i++) {
          out.push({ pos: negShare + (i / k) * (1 - negShare), rgb: color(percentile(nonNeg, i / k)) })
        }
      }
      return out
    },
    quantiles: n
      ? {
          p10: percentile(sorted, 0.1),
          p25: percentile(sorted, 0.25),
          p50: percentile(sorted, 0.5),
          p75: percentile(sorted, 0.75),
          p90: percentile(sorted, 0.9),
        }
      : null,
    lo: n ? sorted[0] : 0, // observed extremes — legend endpoints only
    hi: n ? sorted[n - 1] : 0,
    negShare, // CDF position of 0 € — the legend's zero marker; > 0 ⇔ negatives exist
  }
}
