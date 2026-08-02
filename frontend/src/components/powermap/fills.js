import { priceColor } from './scales'
import { PriceScaleLegend, StateLegend } from './Legend'

// Fill registry — one entry per choropleth fill mode, carrying the FULL
// per-fill contract so a new fill (PR 6) lands purely additively here;
// index.jsx never branches on a fill key:
//   key/label      — header toggle button
//   scrub          — whether the time scrubber applies (grid state is always live)
//   hasLabels      — whether the ZONES view draws the per-zone value TextLayer
//   getColor(zoneKey, ctx) — base RGB for one zone, ctx = {byZone, lo, hi, pal};
//                    null = zone has no data (index falls back to pal.contextFill)
//   alpha          — layer opacities: .zone (choropleth) / .point (dots)
//   Legend         — footer legend row component; always receives {lo, hi, pal}
//   triggers(ctx)  — updateTriggers tail: the identities THIS fill's colors
//                    depend on beyond the shared [fill, effRows, theme]
export const FILLS = [
  {
    key: 'price',
    label: 'DAY-AHEAD €/MWh',
    scrub: true,
    hasLabels: true,
    getColor: (zone, { byZone, lo, hi, pal }) => {
      const z = byZone.get(zone)
      if (!z) return null
      return priceColor(z.price_close, lo, hi, pal)
    },
    alpha: { zone: 235, point: 240 },
    Legend: PriceScaleLegend,
    triggers: ({ lo, hi }) => [lo, hi],
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
