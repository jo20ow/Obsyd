import { ArcLayer } from '@deck.gl/layers'
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
      // Deterministic ±8° fan so parallel Benelux/Nordic arcs do not stack.
      tilt: ((i % 3) - 1) * 8,
    })
  })
  return out
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
