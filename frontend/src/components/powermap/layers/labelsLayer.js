import { TextLayer } from '@deck.gl/layers'
import { CollisionFilterExtension } from '@deck.gl/extensions'

// Per-zone labels for the ZONES view — "DE-LU 74" on the price fill, bare zone
// code on the state fill. Text + cull priority are dispatched through the fill
// registry (fills.js), so this factory never branches on a fill key.
export function makeLabelsLayer({ points, pal, theme, effRows, fill, fillDef }) {
  return new TextLayer({
    id: 'zone-labels',
    data: points.filter((p) => fillDef.labelText(p) != null),
    getPosition: (d) => d.position,
    getText: (d) => fillDef.labelText(d),
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
    // Collision cull: overlapping labels hide instead of overprinting (they
    // reappear on zoom — that's the extension's behavior, no code needed).
    // Which one survives is the fill's call — see labelPriority in fills.js.
    // sizeScale 1.2 tests a slightly fatter footprint, buying breathing room
    // between labels that would otherwise kiss.
    extensions: [new CollisionFilterExtension()],
    collisionGroup: 'zone-labels',
    getCollisionPriority: (d) => fillDef.labelPriority(d),
    collisionTestProps: { sizeScale: 1.2 },
    updateTriggers: {
      getText: [effRows, fill],
      getPosition: [effRows],
      getColor: [theme],
      getCollisionPriority: [effRows, fill],
    },
  })
}
