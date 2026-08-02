import { GeoJsonLayer } from '@deck.gl/layers'

// Choropleth over the real bidding-zone geometry. Features without a `zone`
// property are neighbouring countries — context only: muted fill, no tooltip,
// and they never fire onZoneClick.
//
// The SELECTED zone's outline is deliberately not here: it is its own casing +
// accent pair drawn on top (see selectionLayer). Inside this layer it could only
// ever be one flat colour, and a neighbour's fill could paint over it.
//
// `fillColorTriggers` is the fully assembled getFillColor updateTriggers array
// (index.jsx builds it as [fill, effRows, ...(fillDef.triggers?.(ctx) ?? []), theme]).
export function makeZonesLayer({ geo, zoneFill, pal, theme, fillColorTriggers, onZoneClick }) {
  return new GeoJsonLayer({
    id: 'eu-zones',
    data: geo,
    pickable: true,
    stroked: true,
    filled: true,
    getFillColor: (f) => (f.properties.zone ? zoneFill(f.properties.zone) : pal.contextFill),
    getLineColor: (f) => (f.properties.zone ? pal.zoneLine : pal.contextLine),
    getLineWidth: 1,
    lineWidthUnits: 'pixels',
    lineWidthMinPixels: 1,
    autoHighlight: true,
    highlightColor: pal.highlight,
    onClick: ({ object }) => {
      const zone = object?.properties?.zone
      if (zone) onZoneClick?.(zone)
    },
    updateTriggers: {
      getFillColor: fillColorTriggers,
      getLineColor: [theme],
    },
  })
}

// POINTS view keeps the zone shapes underneath as pure context (uniform fill,
// not pickable). A `.clone()` of the configured layer, so the base props stay
// in lock-step with the main variant by construction. theme stays in the line
// triggers: a theme flip while in POINTS view must repaint the (inherited)
// stroke accessors, not leave stale outlines.
export function makeContextZonesLayer(zonesLayer, { pal, theme }) {
  return zonesLayer.clone({
    pickable: false,
    autoHighlight: false,
    getFillColor: pal.contextFill,
    updateTriggers: {
      getFillColor: [theme],
      getLineColor: [theme],
    },
  })
}
