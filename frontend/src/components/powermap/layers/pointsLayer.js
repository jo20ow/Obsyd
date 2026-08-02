import { ScatterplotLayer } from '@deck.gl/layers'

// POINTS view: one dot per bidding zone (the honest granularity of a zonal
// market). `pointFill` comes from index.jsx, which resolves it through the
// FILLS registry (getColor + alpha.point). `fillColorTriggers` is the same
// assembled getFillColor updateTriggers array the zones layer uses.
export function makePointsLayer({ points, pointFill, pal, theme, fillColorTriggers, onZoneClick }) {
  return new ScatterplotLayer({
    id: 'power-points', data: points, pickable: true,
    getPosition: (d) => d.position, getFillColor: pointFill,
    getRadius: 7, radiusUnits: 'pixels', radiusMinPixels: 5, radiusMaxPixels: 11,
    stroked: true, getLineColor: pal.zoneLine, lineWidthMinPixels: 1,
    onClick: ({ object }) => { if (object?.zone) onZoneClick?.(object.zone) },
    updateTriggers: { getFillColor: fillColorTriggers, getLineColor: [theme] },
  })
}
