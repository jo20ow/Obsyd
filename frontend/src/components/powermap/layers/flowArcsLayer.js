import { ArcLayer, TextLayer } from '@deck.gl/layers'
import { ZONE_COORDS, ARC_MAX_PX, ARC_CONTEXT_MAX_PX, UTIL_MID, UTIL_HIGH, arcWidth } from '../constants'

// One arc per border, carrying the WHOLE border object (the tooltip reads its
// stats). Direction is static: faint end = exporter, solid end = importer.
export function buildArcs(borders, pal) {
  const out = [];
  (borders || []).forEach((b, i) => {
    const ca = ZONE_COORDS[b.zone_a]
    const cb = ZONE_COORDS[b.zone_b]
    if (!ca || !cb) {
      console.warn(`PowerMap arcs: no coordinates for border ${b.zone_a}-${b.zone_b}`)
      return
    }
    const mw = b.latest_flow_mw
    const noFlow = mw == null || mw === 0
    // API sign convention: positive = zone_a → zone_b (canonical sorted pair).
    const flip = !noFlow && mw < 0
    // Color = how loaded the border is; the grays are states, not magnitudes:
    // proxy = no NTC published (flow-based Core / Nordics), none = no reading.
    // `informative` = the arc carries a real NTC-utilization reading. The gray
    // arcs are demoted to quiet context (2 px cap, lower alpha): 40 of 63
    // borders are gray by MARKET DESIGN, not data failure, and at full √ width
    // they drowned the handful of arcs that actually say something.
    let rgb
    let informative = false
    if (noFlow) rgb = pal.arc.none
    else if (b.capacity_source !== 'ntc') rgb = pal.arc.proxy
    else if (b.util_latest_pct == null) rgb = pal.arc.none
    else {
      informative = true
      if (b.util_latest_pct < UTIL_MID) rgb = pal.arc.low
      else if (b.util_latest_pct < UTIL_HIGH) rgb = pal.arc.mid
      else rgb = pal.arc.high
    }
    // No reading → uniform faint alpha at both ends: a gradient would claim
    // a direction we do not have. Still pickable/clickable (widthMinPixels 1).
    let sourceAlpha, targetAlpha
    if (noFlow) { sourceAlpha = 60; targetAlpha = 60 }
    else if (informative) { sourceAlpha = 70; targetAlpha = 235 }
    else { sourceAlpha = 40; targetAlpha = 110 }
    out.push({
      ...b,
      source: flip ? cb : ca,
      target: flip ? ca : cb,
      width: noFlow ? 1 : informative ? arcWidth(mw) : Math.min(arcWidth(mw), ARC_CONTEXT_MAX_PX),
      sourceColor: [...rgb, sourceAlpha],
      targetColor: [...rgb, targetAlpha],
      // Chevron inputs: only arcs with a real reading claim a direction, and
      // the glyph is solid — it exists precisely because the faint→solid
      // gradient alone was not readable without hovering.
      hasDirection: !noFlow,
      arrowColor: [...rgb, informative ? 245 : 170],
      // Deterministic ±8° fan so parallel Benelux/Nordic arcs do not stack.
      tilt: ((i % 3) - 1) * 8,
    })
  })
  return out
}

// Direction chevron: one ▶ near the importer end of every arc that carries a
// flow reading (owner feedback 2026-09-11: the faint→solid gradient forces a
// hover to learn the direction — the chevron says it without one). It sits at
// 88% of the CHORD, angled along it; the arc's bow (getHeight 0.4, tilt ±8°)
// deviates little that close to the endpoint, so the glyph reads as riding the
// line. Not pickable — the arc underneath keeps hover and click.
const ARROW_T = 0.88
const arrowPosition = (d) => [
  d.source[0] + (d.target[0] - d.source[0]) * ARROW_T,
  d.source[1] + (d.target[1] - d.source[1]) * ARROW_T,
]
// Chord bearing in screen terms: east is +x, north is +y, and the longitude
// leg shrinks by cos(lat) — without that correction every northern arrow
// points visibly off its arc.
const arrowAngle = (d) => {
  const dx = (d.target[0] - d.source[0]) *
    Math.cos((((d.source[1] + d.target[1]) / 2) * Math.PI) / 180)
  return (Math.atan2(d.target[1] - d.source[1], dx) * 180) / Math.PI
}

export function makeFlowArrowsLayer({ arcs }) {
  return new TextLayer({
    id: 'border-arc-arrows',
    data: arcs.filter((d) => d.hasDirection),
    pickable: false,
    billboard: false, // rotate in the map plane, not toward the camera
    getPosition: arrowPosition,
    getText: () => '▶',
    getColor: (d) => d.arrowColor,
    getAngle: arrowAngle,
    getSize: (d) => Math.max(9, Math.min(14, 7 + d.width * 1.2)),
    sizeUnits: 'pixels',
    fontFamily: 'sans-serif',
    characterSet: ['▶'], // default atlas is ASCII-only; without this the glyph is a tofu box
    updateTriggers: {
      getPosition: [arcs], getColor: [arcs], getAngle: [arcs], getSize: [arcs],
    },
  })
}

export function makeFlowArcsLayer({ arcs, pal, onBorderSelect }) {
  return new ArcLayer({
    id: 'border-arcs',
    data: arcs,
    pickable: true,
    autoHighlight: true,
    highlightColor: pal.highlight,
    getSourcePosition: (d) => d.source,
    getTargetPosition: (d) => d.target,
    getSourceColor: (d) => d.sourceColor,
    getTargetColor: (d) => d.targetColor,
    getWidth: (d) => d.width,
    widthUnits: 'pixels',
    widthMinPixels: 1,
    widthMaxPixels: ARC_MAX_PX,
    getHeight: 0.4,
    getTilt: (d) => d.tilt,
    onClick: ({ object }) => { if (object) onBorderSelect?.(object.zone_a, object.zone_b) },
    updateTriggers: {
      getSourceColor: [arcs], getTargetColor: [arcs], getWidth: [arcs],
      getTilt: [arcs], getSourcePosition: [arcs], getTargetPosition: [arcs],
    },
  })
}
