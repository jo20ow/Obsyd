import { useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { InfoPopover } from '../Panel'
import { useTheme } from '../../context/ThemeContext'
import { PALETTES } from './palettes'
import { ZONE_COORDS, INITIAL_VIEW } from './constants'
import { collectWeekValues, makeQuantileScale } from './scales'
import { FILLS } from './fills'
import useMapData from './useMapData'
import { makeTooltip } from './tooltip'
import { makeZonesLayer, makeContextZonesLayer } from './layers/zonesLayer'
import { makeLabelsLayer } from './layers/labelsLayer'
import { buildArcs, makeFlowArcsLayer } from './layers/flowArcsLayer'
import { makePointsLayer } from './layers/pointsLayer'
import { FlowArcLegend } from './Legend'
import Scrubber from './Scrubber'

// Header toggle chip — the ONE source for the active/idle button styling
// (ZONES/POINTS, the fill buttons, FLOWS; later PRs add more).
function ToggleButton({ active, onClick, title, children }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
        active ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'
      }`}
    >
      {children}
    </button>
  )
}

// Container: owns the map state (fill, view, overlays, scrub index), composes
// the data hook + layer factories + chrome. Everything fill-specific (color,
// alpha, legend, labels, scrub, triggers) is dispatched through the FILLS
// registry — no fill-key branches live here. `onZoneSelect`/`selectedZone`/
// `tall` are optional and no-ops until the desk threads them (PR 8).
export default function PowerMap({ onBorderSelect, onZoneSelect, selectedZone, tall = false }) {
  const { theme } = useTheme()
  const pal = PALETTES[theme] || PALETTES.dark
  const [fill, setFill] = useState('price')
  const { geo, rows, snap, borders, extra, errors } = useMapData(fill)
  const [view, setView] = useState('zones') // 'zones' choropleth | 'points' per-zone dots
  const [idx, setIdx] = useState(null) // selected hour index; null = latest/live
  // Map overlays: flows = cross-border arcs, labels = per-zone TextLayer.
  const [overlays, setOverlays] = useState({ flows: true, labels: true })

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
    const zonesLayer = makeZonesLayer({
      geo, zoneFill, pal, theme, fillColorTriggers, selectedZone, onZoneClick: onZoneSelect,
    })
    if (view === 'points') {
      // Every point's zone is in byZone by construction (both derive from
      // effRows), so the pal.mid fallback is only defensive.
      const pointFill = (p) => [...(fillDef.getColor(p.zone, fillCtx) || pal.mid), fillDef.alpha.point]
      return [
        makeContextZonesLayer(zonesLayer, { pal, theme, selectedZone }),
        ...(arcLayer ? [arcLayer] : []),
        makePointsLayer({ points, pointFill, pal, theme, fillColorTriggers, onZoneClick: onZoneSelect }),
      ]
    }
    const labels = overlays.labels && fillDef.labelText
      ? makeLabelsLayer({ points, pal, theme, effRows, fill, fillDef, fillCtx })
      : null
    const base = labels ? [zonesLayer, labels] : [zonesLayer]
    return arcLayer ? [...base, arcLayer] : base
  }, [geo, view, fill, fillDef, fillCtx, effRows, points, theme, arcs, overlays, atLatest, onBorderSelect, onZoneSelect, selectedZone, pal])

  const getTooltip = useMemo(() => makeTooltip(byZone, pal, fillDef, fillCtx), [byZone, pal, fillDef, fillCtx])

  const zoneCount = byZone.size

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[12px] font-semibold text-neutral-300">Europe · power map</span>
          <InfoPopover text="Real bidding-zone geometry (SE1–SE4, NO1–NO5, Italian sub-zones), shaded by the day-ahead price — or by grid state, or by the technology setting the price. IMPORTANT: it shades ONE HOUR at a time (the hour on the slider below), not the whole day — so a zone can read €0 here at 08:00 while the all-zones table shows a positive daily mean. Drag the slider to move through the hours. Fixed equal-frequency colour scale across the shown week: colours spread by rank among the week's all-zone hours, not by € distance (tooltips carry exact €) — violet = negative prices (a distinct state, not just cheap), brighter cyan = more expensive. Flat unlabelled shapes = neighbouring countries, no data by design. PRICE-SETTING TECH shades each zone by the technology estimated to have set its latest price — a fixed-merit-order attribution in the generation-mix fuel colours, not a cost model (a slate zone is in scope but has no value, and each zone carries its own hour — hover it; glossary in HOW TO READ). FLOWS arcs = the latest cross-border flow per border: the faint end exports, the solid end imports; width ∝ GW, colour = how loaded the border is vs its offered day-ahead capacity (grey = no NTC published or no reading — drawn thin and faint as context); they always show the latest hour and hide while you scrub the past — click one for the border detail below. Zone geometry © Electricity Maps contributors (AGPL). Data: ENTSO-E. Descriptive, not a forecast." />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            {[['zones', 'ZONES'], ['points', 'POINTS']].map(([v, l]) => (
              <ToggleButton key={v} active={view === v} onClick={() => setView(v)}>{l}</ToggleButton>
            ))}
          </div>
          <div className="flex items-center gap-1">
            {FILLS.map((m) => (
              <ToggleButton key={m.key} active={fill === m.key} onClick={() => setFill(m.key)}>{m.label}</ToggleButton>
            ))}
          </div>
          <ToggleButton
            active={overlays.flows}
            onClick={() => setOverlays((o) => ({ ...o, flows: !o.flows }))}
            title="Cross-border flow arcs (latest hour)"
          >
            FLOWS
          </ToggleButton>
          {view === 'zones' && fillDef.labelText && (
            <ToggleButton
              active={overlays.labels}
              onClick={() => setOverlays((o) => ({ ...o, labels: !o.labels }))}
              title="Per-zone labels (overlapping labels hide — zoom in to reveal)"
            >
              LABELS
            </ToggleButton>
          )}
        </div>
      </div>

      <div
        className={`relative ${tall ? 'h-[60vh] lg:h-[min(75vh,calc(100vh-230px))]' : ''}`}
        style={tall ? { background: pal.surface } : { height: 460, background: pal.surface }}
      >
        {/* pickingRadius: the demoted 2 px context arcs stay clickable without
            pixel-hunting — widens hit-testing only, no visual change. */}
        <DeckGL initialViewState={INITIAL_VIEW} controller={true} layers={layers} getTooltip={getTooltip} pickingRadius={4} />
      </div>

      {fillDef.scrub && ts.length > 1 && <Scrubber ts={ts} effIdx={effIdx} setIdx={setIdx} />}

      {overlays.flows && <FlowArcLegend pal={pal} atLatest={atLatest} />}

      <div className="flex items-center justify-between gap-2 px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-600">
        {/* The active fill's feed error travels WITH its payload: a dead feed
            must be legible in the legend, not just in the console. */}
        <fillDef.Legend scale={scale} pal={pal} extra={extra} extraError={errors.extra} />
        <span>{zoneCount} zones · ENTSO-E · zones © Electricity Maps</span>
      </div>
    </div>
  )
}
