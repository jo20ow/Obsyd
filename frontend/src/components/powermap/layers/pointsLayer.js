import { ScatterplotLayer } from '@deck.gl/layers'

// POINTS view: one dot per bidding zone (the honest granularity of a zonal
// market). `pointFill` stays a closure in index.jsx next to zoneFill, so the
// color logic lives in one place until a later PR moves it into fills.js.
export function makePointsLayer({ points, pointFill, pal, theme, fill, effRows, lo, hi, onZoneClick }) {
  return new ScatterplotLayer({
    id: 'power-points', data: points, pickable: true,
    getPosition: (d) => d.position, getFillColor: pointFill,
    getRadius: 7, radiusUnits: 'pixels', radiusMinPixels: 5, radiusMaxPixels: 11,
    stroked: true, getLineColor: pal.zoneLine, lineWidthMinPixels: 1,
    onClick: ({ object }) => { if (object?.zone) onZoneClick?.(object.zone) },
    updateTriggers: { getFillColor: [fill, effRows, lo, hi, theme], getLineColor: [theme] },
  })
}
