import { TextLayer } from '@deck.gl/layers'

// Per-zone price labels for the ZONES view (price fill only).
export function makeLabelsLayer({ points, pal, theme, effRows }) {
  return new TextLayer({
    id: 'zone-price-labels',
    data: points.filter((p) => p.price != null),
    getPosition: (d) => d.position,
    getText: (d) => `${Math.round(d.price)}`,
    getColor: pal.label,
    outlineColor: pal.labelOutline,
    outlineWidth: 2,
    fontSettings: { sdf: true },
    fontFamily: 'ui-monospace, Menlo, monospace',
    // Meters, not pixels: labels grow with zoom, so the dense Benelux/Baltic
    // cluster stays quiet at continent zoom and becomes readable on approach.
    sizeUnits: 'meters',
    getSize: 60000,
    sizeMaxPixels: 13,
    billboard: true,
    pickable: false,
    updateTriggers: { getText: [effRows], getPosition: [effRows], getColor: [theme] },
  })
}
