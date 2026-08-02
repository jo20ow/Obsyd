import { GeoJsonLayer } from '@deck.gl/layers'

// Choropleth over the real bidding-zone geometry. Features without a `zone`
// property are neighbouring countries — context only: muted fill, no tooltip,
// and they never fire onZoneClick. NOTE: the geojson `zone` property is NOT
// unique — DE_LU spans two features (DE + LU), FR two (FR + FR-COR) — so the
// selectedZone outline matches by property value and lights up ALL of a
// zone's features.
//
// `fillColorTriggers` is the fully assembled getFillColor updateTriggers array
// (index.jsx builds it as [fill, effRows, ...(fillDef.triggers?.(ctx) ?? []), theme]).
export function makeZonesLayer({ geo, zoneFill, pal, theme, fillColorTriggers, selectedZone, onZoneClick }) {
  return new GeoJsonLayer({
    id: 'eu-zones',
    data: geo,
    pickable: true,
    stroked: true,
    filled: true,
    getFillColor: (f) => (f.properties.zone ? zoneFill(f.properties.zone) : pal.contextFill),
    getLineColor: (f) => {
      if (!f.properties.zone) return pal.contextLine
      if (selectedZone && f.properties.zone === selectedZone) return [...pal.posPole, 255]
      return pal.zoneLine
    },
    // Selected-zone accent outline: thicker stroke on the matching feature(s).
    // A strict no-op while selectedZone is null/undefined — 2.5 px only when
    // selected, otherwise 1 px (== the lineWidthMinPixels floor as before).
    getLineWidth: (f) => (selectedZone && f.properties.zone === selectedZone ? 2.5 : 1),
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
      getLineColor: [theme, selectedZone],
      getLineWidth: [selectedZone],
    },
  })
}

// POINTS view keeps the zone shapes underneath as pure context (uniform fill,
// not pickable). A `.clone()` of the configured layer, so the base props stay
// in lock-step with the main variant by construction. theme stays in the line
// triggers: a theme flip while in POINTS view must repaint the (inherited)
// stroke accessors, not leave stale outlines.
export function makeContextZonesLayer(zonesLayer, { pal, theme, selectedZone }) {
  return zonesLayer.clone({
    pickable: false,
    autoHighlight: false,
    getFillColor: pal.contextFill,
    updateTriggers: {
      getFillColor: [theme],
      getLineColor: [theme, selectedZone],
      getLineWidth: [theme, selectedZone],
    },
  })
}
