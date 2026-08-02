import { PriceScaleLegend, StateLegend } from './Legend'

// Fill registry — one entry per choropleth fill mode, carrying the FULL
// per-fill contract so a new fill (PR 6) lands purely additively here;
// index.jsx never branches on a fill key:
//   key/label      — header toggle button
//   scrub          — whether the time scrubber applies (grid state is always live)
//   hasLabels      — whether the ZONES view offers the per-zone TextLayer
//                    (+ the LABELS toggle in the header)
//   labelText(pt)  — label string for one point row; null = no label for this
//                    point (price fill skips zones without a price)
//   labelPriority(pt) — collision-cull rank: when labels overlap, the HIGHER
//                    value survives. Must stay within deck.gl's −1000..1000.
//   getColor(zoneKey, ctx) — base RGB for one zone, ctx = {byZone, scale, pal};
//                    null = zone has no data (index falls back to pal.contextFill)
//   alpha          — layer opacities: .zone (choropleth) / .point (dots)
//   Legend         — footer legend row component; always receives {scale, pal}
//   triggers(ctx)  — updateTriggers tail: the identities THIS fill's colors
//                    depend on beyond the shared [fill, effRows, theme]
export const FILLS = [
  {
    key: 'price',
    label: 'DAY-AHEAD €/MWh',
    scrub: true,
    hasLabels: true,
    labelText: (p) => (p.price == null ? null : `${p.label} ${Math.round(p.price)}`),
    // |price| so the EXTREME zones survive the collision cull — a €300 spike or
    // a €−40 solar dump is exactly the label you want in the dense Benelux
    // cluster, not whichever zone happens to draw first. Clamped: spike hours
    // exceed 1000 €/MWh and deck.gl's priority range ends there.
    labelPriority: (p) => Math.min(Math.abs(p.price ?? 0), 1000),
    getColor: (zone, { byZone, scale }) => {
      const z = byZone.get(zone)
      if (!z) return null
      return scale.color(z.price_close)
    },
    alpha: { zone: 235, point: 240 },
    Legend: PriceScaleLegend,
    // The scale object is memoized on [snap, rows, pal] in index.jsx — its
    // identity IS the domain version, so a new week population repaints.
    triggers: ({ scale }) => [scale],
  },
  {
    key: 'state',
    label: 'GRID STATE',
    scrub: false,
    hasLabels: true,
    // State is already the color — the label only answers "which zone is that",
    // so it's the bare zone code. Cull priority = severity: a STRESSED zone's
    // name must not lose the overlap fight to a CALM neighbour.
    labelText: (p) => p.label,
    labelPriority: (p) => ({ CALM: 0, ELEVATED: 1, STRESSED: 2 })[p.state] ?? 0,
    getColor: (zone, { byZone, pal }) => {
      const z = byZone.get(zone)
      if (!z) return null
      return pal.state[z.state] || pal.mid
    },
    alpha: { zone: 215, point: 240 },
    Legend: StateLegend,
    triggers: () => [],
  },
]
