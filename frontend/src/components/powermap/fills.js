import { PriceScaleLegend, StateLegend } from './Legend'

// Fill registry — one entry per choropleth fill mode, carrying the FULL
// per-fill contract so a new fill (PR 6) lands purely additively here;
// index.jsx never branches on a fill key:
//   key/label      — header toggle button
//   scrub          — whether the time scrubber applies (grid state is always live)
//   hasLabels      — whether the ZONES view draws the per-zone value TextLayer
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
    hasLabels: false,
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
