import { GeoJsonLayer } from '@deck.gl/layers'
import { SELECTION_WIDTH_PX, SELECTION_CASING_PX } from '../constants'

// The selected zone's contour, drawn as its OWN pair of layers rather than as a
// branch inside the choropleth's stroke accessors. Two reasons:
//   1. Legibility. A single accent cannot work: pal.posPole IS the price ramp's
//      expensive end, so the old accent-only outline sat at ΔE 0.0 on exactly
//      the zones worth inspecting. A casing under the accent makes the contour
//      two-toned, so one tone always separates from the fill (see constants).
//   2. Stacking. Inside the choropleth every polygon is one layer, so a
//      neighbour's fill can paint over the selected zone's stroke. On top, the
//      contour is never half-covered.
// NOT pickable: the choropleth underneath owns hover + click, and a transparent
// contour layer on top would swallow both.
// NOTE: the geojson `zone` property is NOT unique (DE_LU spans DE + LU, FR spans
// FR + FR-COR), so this filters — every feature of the zone gets the contour.
export function makeSelectionLayers({ geo, selectedZone, pal }) {
  if (!geo || !selectedZone) return []
  const features = geo.features.filter((f) => f.properties.zone === selectedZone)
  if (features.length === 0) return []
  const data = { type: 'FeatureCollection', features }

  // No updateTriggers: both stroke accessors below are CONSTANTS, not per-feature
  // functions, so there is nothing for deck.gl to cache and re-evaluate — a
  // constant accessor is re-read whenever the prop itself changes. What actually
  // varies here is `data` (a new selection is a new filtered FeatureCollection,
  // and these layers are not created at all when nothing is selected) and the
  // palette (a theme flip changes pal, hence the constants themselves).
  const base = {
    data,
    pickable: false,
    stroked: true,
    filled: false,
    lineWidthUnits: 'pixels',
    lineJointRounded: true,
    lineCapRounded: true,
  }

  return [
    new GeoJsonLayer({
      ...base,
      id: 'eu-zone-selected-casing',
      getLineColor: pal.labelOutline,
      // Centred strokes: the casing shows SELECTION_CASING_PX/2 on each side.
      getLineWidth: SELECTION_WIDTH_PX + SELECTION_CASING_PX,
    }),
    new GeoJsonLayer({
      ...base,
      id: 'eu-zone-selected',
      getLineColor: [...pal.posPole, 255],
      getLineWidth: SELECTION_WIDTH_PX,
    }),
  ]
}
