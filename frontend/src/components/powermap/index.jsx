import { useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { useTheme } from '../../context/ThemeContext'
import { PALETTES } from './palettes'
import { ZONE_COORDS, INITIAL_VIEW } from './constants'
import { collectWeekValues, makeQuantileScale } from './scales'
import { FILLS } from './fills'
import useMapData from './useMapData'
import { makeTooltip } from './tooltip'
import { makeZonesLayer, makeContextZonesLayer } from './layers/zonesLayer'
import { makeSelectionLayers } from './layers/selectionLayer'
import { makeLabelsLayer } from './layers/labelsLayer'
import { buildArcs, makeFlowArcsLayer } from './layers/flowArcsLayer'
import { makePointsLayer } from './layers/pointsLayer'
import { buildOutagePaths, makeOutageLayers, outageTooltip } from './layers/outageLayer'
import { FlowArcLegend, OutageLegend } from './legends'
import useFetchWithError from '../../hooks/useFetchWithError'
import Scrubber from './Scrubber'
import MapHeader from './MapHeader'

// The A78 overlay's own feed. NOT in useMapData's EXTRA_BY_FILL: that seam is
// keyed by the active FILL, and this is an overlay — it is owned by the
// `overlays.outages` toggle that lives in this component, and it must survive
// every fill switch. Fetched only while the overlay is on (falsy url = idle,
// see useFetchWithError's docblock); the url-keyed SWR cache repaints it
// instantly on the way back on.
const OUTAGES_URL = '/api/power/outages/transmission'
// Overlay tooltips are tried before the fill/zone branches — one stable array
// so the memo below is not invalidated on every render.
const OVERLAY_TIPS = [outageTooltip]

// Container: owns the map state (fill, view, overlays, scrub index), composes
// the data hook + layer factories + chrome (MapHeader). Everything fill-specific
// (color, alpha, legend, labels, scrub, triggers, ⓘ copy) is dispatched through
// the FILLS registry — no fill-key branches live here.
//   selectedZone / onZoneSelect — the desk's zone selection, two-way: the rail's
//     table highlights the outlined zone and a click on the map selects the row
//     (EuropeDesk owns the state). Both optional; the map is standalone without.
//   tall — the desk-split layout's map column, where the map is the page's
//     subject and gets the viewport height it deserves.
export default function PowerMap({ onBorderSelect, onZoneSelect, selectedZone, tall = false }) {
  const { theme } = useTheme()
  const pal = PALETTES[theme] || PALETTES.dark
  const [fill, setFill] = useState('price')
  const { geo, rows, snap, borders, extra, errors } = useMapData(fill)
  const [view, setView] = useState('zones') // 'zones' choropleth | 'points' per-zone dots
  const [idx, setIdx] = useState(null) // selected hour index; null = latest/live
  // Map overlays: flows = cross-border arcs, labels = per-zone TextLayer,
  // outages = A78 transmission-outage chords. Outages default OFF: several
  // hundred events is a lot of ink to put on the map unasked.
  const [overlays, setOverlays] = useState({ flows: true, labels: true, outages: false })

  const { data: outageFeed, error: outageError } = useFetchWithError(overlays.outages ? OUTAGES_URL : null)
  useEffect(() => { if (outageError) console.error('PowerMap outages:', outageError) }, [outageError])

  const fillDef = FILLS.find((f) => f.key === fill) || FILLS[0]

  const ts = snap?.timestamps || []
  const effIdx = idx == null ? ts.length - 1 : idx
  // Arcs always show the LATEST flow. While the scrubber sits on a past hour the
  // choropleth shows that hour — latest arcs on top of it would lie, so they hide.
  // (The scrubber only exists for fills with scrub:true; grid state is always live.)
  const atLatest = !fillDef.scrub || ts.length === 0 || effIdx === ts.length - 1

  // When scrubbing (a scrub-capable fill + snapshot loaded), override each zone's
  // price with the day-ahead price at the selected hour; otherwise use the live overview.
  const effRows = useMemo(() => {
    if (!fillDef.scrub || !snap?.zones || ts.length === 0) return rows || []
    return (rows || []).map((z) => {
      const col = snap.zones[z.zone]
      const v = col ? col[effIdx] : undefined
      return v == null ? z : { ...z, price_close: v }
    })
  }, [rows, snap, effIdx, fillDef, ts.length])

  const byZone = useMemo(() => {
    const m = new Map()
    for (const z of effRows) m.set(z.zone, z)
    return m
  }, [effRows])

  // Week-fixed equal-frequency color scale, built ONCE from the whole window's
  // population (all zones × all hours + live rows) so scrubbing repaints hours
  // against the same mapping — see makeQuantileScale in scales.js. Its object
  // identity doubles as the repaint trigger for the price fill.
  const scale = useMemo(() => makeQuantileScale(collectWeekValues(snap, rows), pal), [snap, rows, pal])

  const points = useMemo(() => {
    const pts = []
    for (const z of effRows) {
      const c = ZONE_COORDS[z.zone]
      if (!c) continue
      pts.push({ position: c, zone: z.zone, label: z.zone_label || z.zone, price: z.price_close, state: z.state })
    }
    return pts
  }, [effRows])

  const arcs = useMemo(() => buildArcs(borders, pal), [borders, pal])

  // Several hundred events bucket down to a few dozen border chords — memoized
  // on the PAYLOAD's identity (stable per fetch), never per render.
  const { paths: outagePaths, counts: outageCounts } = useMemo(
    () => buildOutagePaths(outageFeed, pal), [outageFeed, pal]
  )

  // The ONE ctx every fill hook sees (color, labels, tooltip lines) — hoisted
  // out of the layers memo so the tooltip reads from the identical object.
  const fillCtx = useMemo(() => ({ byZone, scale, pal, extra }), [byZone, scale, pal, extra])

  const layers = useMemo(() => {
    if (!geo) return []
    const zoneFill = (zone) => {
      const rgb = fillDef.getColor(zone, fillCtx)
      return rgb ? [...rgb, fillDef.alpha.zone] : pal.contextFill
    }
    // Shared identity inputs + the active fill's own tail (e.g. the scale for price).
    const fillColorTriggers = [fill, effRows, ...(fillDef.triggers?.(fillCtx) ?? []), theme]
    const arcLayer = overlays.flows && atLatest && arcs.length > 0
      ? makeFlowArcsLayer({ arcs, pal, onBorderSelect })
      : null
    // Deliberately NOT gated on `atLatest`, unlike the arcs: an outage is a
    // WINDOW, so "this line is out right now" stays true whichever past hour
    // the choropleth is painting. The legend says so while scrubbing.
    // Drawn BELOW the arcs on purpose — the arcs are the always-on default, so
    // where the two geometries converge near the endpoints the arc keeps the
    // hover. Both carry the SAME click (onBorderSelect), so the chords covering
    // the arcs' hit area costs nothing: either mark opens the same border row.
    const outageLayers = overlays.outages && outagePaths.length > 0
      ? makeOutageLayers({ paths: outagePaths, pal, onBorderSelect })
      : []
    const zonesLayer = makeZonesLayer({
      geo, zoneFill, pal, theme, fillColorTriggers, onZoneClick: onZoneSelect,
    })
    // Straight above the choropleth, below every overlay: the contour marks
    // which zone the rail is looking at, but the arcs/chords/labels are the
    // information ON the map and keep the top of the stack.
    const selectionLayers = makeSelectionLayers({ geo, selectedZone, pal })
    if (view === 'points') {
      // Every point's zone is in byZone by construction (both derive from
      // effRows), so the pal.mid fallback is only defensive.
      const pointFill = (p) => [...(fillDef.getColor(p.zone, fillCtx) || pal.mid), fillDef.alpha.point]
      return [
        makeContextZonesLayer(zonesLayer, { pal, theme }),
        ...selectionLayers,
        ...outageLayers,
        ...(arcLayer ? [arcLayer] : []),
        makePointsLayer({ points, pointFill, pal, theme, fillColorTriggers, onZoneClick: onZoneSelect }),
      ]
    }
    const labels = overlays.labels && fillDef.labelText
      ? makeLabelsLayer({ points, pal, theme, effRows, fill, fillDef, fillCtx })
      : null
    // Labels ride ABOVE the outage chords (they carry an outline halo and are
    // not pickable, so they stay readable without stealing a hover).
    const base = [zonesLayer, ...selectionLayers, ...outageLayers, ...(labels ? [labels] : [])]
    return arcLayer ? [...base, arcLayer] : base
  }, [geo, view, fill, fillDef, fillCtx, effRows, points, theme, arcs, outagePaths, overlays, atLatest, onBorderSelect, onZoneSelect, selectedZone, pal])

  const getTooltip = useMemo(
    () => makeTooltip({ byZone, pal, fillDef, fillCtx, overlayTips: OVERLAY_TIPS }),
    [byZone, pal, fillDef, fillCtx]
  )

  const zoneCount = byZone.size

  return (
    // `tall` on lg: the CARD is exactly one viewport minus the desk's 12 px top
    // offset and 12 px of air, and the canvas takes whatever the chrome leaves
    // — a flex column, not a magic constant. The chrome is NOT a fixed height:
    // the header and the legends flex-wrap, so it measures 142 px at 1920 wide
    // and 298 px at 1024, and grows again when LINE OUTAGES adds its legend.
    // Any `calc(100vh - <constant>)` for the CANVAS is therefore wrong at most
    // widths — it overflowed the viewport by 15 px at 1280×800.
    // The height must be DEFINITE (h-, not max-h-): `flex-1` distributes free
    // space, and a max-height leaves none to distribute — under max-h the canvas
    // collapsed onto its own floor (360 px) at every desktop width, i.e. the
    // exact "map is too small" this layout exists to fix.
    // The max() floor is for short-but-wide viewports: below it the card simply
    // exceeds the viewport (and scrolls) instead of squeezing the map flat.
    <div className={`border border-border bg-surface rounded overflow-hidden shadow-sm ${
      tall ? 'lg:flex lg:flex-col lg:h-[max(560px,calc(100vh-1.5rem))]' : ''
    }`}>
      <MapHeader
        view={view} setView={setView}
        fill={fill} setFill={setFill} fillDef={fillDef}
        overlays={overlays} setOverlays={setOverlays}
      />

      {/* min-h-0 lets the canvas yield to the chrome rather than push the
          scrubber/legends out of the clipped card. */}
      <div
        className={`relative ${tall ? 'h-[60vh] lg:h-auto lg:flex-1 lg:min-h-0' : ''}`}
        style={tall ? { background: pal.surface } : { height: 460, background: pal.surface }}
      >
        {/* pickingRadius: the demoted 2 px context arcs stay clickable without
            pixel-hunting — widens hit-testing only, no visual change. */}
        <DeckGL initialViewState={INITIAL_VIEW} controller={true} layers={layers} getTooltip={getTooltip} pickingRadius={4} />
      </div>

      {fillDef.scrub && ts.length > 1 && <Scrubber ts={ts} effIdx={effIdx} setIdx={setIdx} />}

      {overlays.flows && <FlowArcLegend pal={pal} atLatest={atLatest} />}

      {overlays.outages && (
        <OutageLegend pal={pal} counts={outageCounts} meta={outageFeed} error={outageError} atLatest={atLatest} />
      )}

      <div className="flex items-center justify-between gap-2 px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-600">
        {/* The active fill's feed error travels WITH its payload: a dead feed
            must be legible in the legend, not just in the console. */}
        <fillDef.Legend scale={scale} pal={pal} extra={extra} extraError={errors.extra} />
        <span>{zoneCount} zones · ENTSO-E · zones © Electricity Maps</span>
      </div>
    </div>
  )
}
