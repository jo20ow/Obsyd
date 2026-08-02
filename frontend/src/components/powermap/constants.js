// Approximate [lon, lat] centroid per bidding zone for the POINTS view. Europe is a
// ZONAL market — one price per bidding zone, not nodal like the US — so one dot per
// zone is the honest granularity. IT_CALABRIA has no polygon in the zone geometry
// (it is part of IT-SO there) and appears on the map only as a point.
export const ZONE_COORDS = {
  DE_LU: [10.4, 51.2], FR: [2.3, 46.6], NL: [5.3, 52.2], BE: [4.5, 50.6], AT: [14.5, 47.6],
  ES: [-3.7, 40.3], PT: [-8.0, 39.5], PL: [19.1, 52.1], CZ: [15.5, 49.8], HU: [19.5, 47.2],
  RO: [25.0, 45.9], GR: [22.0, 39.3], IE_SEM: [-8.0, 53.4], BG: [25.3, 42.7], HR: [15.8, 45.4],
  SI: [14.8, 46.1], SK: [19.7, 48.7], FI: [25.7, 62.5], CH: [8.2, 46.8],
  IT_NORD: [9.5, 45.5], IT_CENTRO_NORD: [11.3, 43.8], IT_CENTRO_SUD: [13.0, 42.0],
  IT_SUD: [16.0, 40.8], IT_CALABRIA: [16.3, 39.0], IT_SICILIA: [14.1, 37.5], IT_SARDEGNA: [9.1, 40.1],
  DK1: [9.3, 56.1], DK2: [12.3, 55.5],
  NO1: [10.5, 60.5], NO2: [7.5, 58.9], NO3: [10.5, 63.2], NO4: [18.5, 68.5], NO5: [6.0, 60.6],
  SE1: [20.0, 66.5], SE2: [17.0, 63.8], SE3: [16.5, 59.3], SE4: [13.5, 56.0],
}

export const INITIAL_VIEW = { longitude: 9, latitude: 54, zoom: 3.1, minZoom: 2.5, maxZoom: 6 }

// ── Cross-border flow arcs ────────────────────────────────────────────────────
// Width encodes |latest flow|: √ scale (a 4× flow reads 2× wide — GW differences
// stay legible without 5-GW borders drowning 300-MW ones), capped at 5 GW / 6 px,
// 1 px floor so thin borders stay hoverable.
export const FLOW_WIDTH_MAX_MW = 5000
export const ARC_MAX_PX = 6
// Gray context arcs (no NTC / no reading) cap here instead: they carry no load
// signal, so they must not out-shout the informative NTC-colored arcs — see
// buildArcs in layers/flowArcsLayer.js.
export const ARC_CONTEXT_MAX_PX = 2
// NTC-utilization classing thresholds (%) — single source for the arc colors
// AND the footer legend, so the two can never drift apart.
export const UTIL_MID = 70
export const UTIL_HIGH = 90
export const arcWidth = (mw) =>
  Math.max(1, ARC_MAX_PX * Math.sqrt(Math.min(Math.abs(mw), FLOW_WIDTH_MAX_MW) / FLOW_WIDTH_MAX_MW))

export function fmtTs(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC',
  })
}
