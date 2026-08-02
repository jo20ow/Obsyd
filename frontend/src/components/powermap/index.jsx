import { useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { InfoPopover } from '../Panel'
import { useTheme } from '../../context/ThemeContext'
import { PALETTES } from './palettes'
import { ZONE_COORDS, INITIAL_VIEW } from './constants'
import { priceColor, percentile } from './scales'
import { FILLS } from './fills'
import useMapData from './useMapData'
import { makeTooltip } from './tooltip'
import { makeZonesLayer, makeContextZonesLayer } from './layers/zonesLayer'
import { makeLabelsLayer } from './layers/labelsLayer'
import { buildArcs, makeFlowArcsLayer } from './layers/flowArcsLayer'
import { makePointsLayer } from './layers/pointsLayer'
import { FlowArcLegend, PriceScaleLegend, StateLegend } from './Legend'
import Scrubber from './Scrubber'

// Container: owns the map state (fill, view, overlays, scrub index), composes
// the data hook + layer factories + chrome. `onZoneSelect`/`selectedZone`/`tall`
// are optional and no-ops until the desk threads them (PR 8).
export default function PowerMap({ onBorderSelect, onZoneSelect, selectedZone, tall = false }) {
  const { theme } = useTheme()
  const pal = PALETTES[theme] || PALETTES.dark
  const { geo, rows, snap, borders } = useMapData()
  const [fill, setFill] = useState('price')
  const [view, setView] = useState('zones') // 'zones' choropleth | 'points' per-zone dots
  const [idx, setIdx] = useState(null) // selected hour index; null = latest/live
  const [overlays, setOverlays] = useState({ flows: true }) // map overlays; flows = cross-border arcs

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
  }, [rows, snap, effIdx, fill, ts.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const byZone = useMemo(() => {
    const m = new Map()
    for (const z of effRows) m.set(z.zone, z)
    return m
  }, [effRows])

  // FIXED color domain over the whole 7-day window (all zones × all hours), so
  // scrubbing compares hours honestly — a per-frame min/max would repaint every
  // zone each step and make yesterday incomparable to today. p2/p98 clamp keeps
  // one spike hour from crushing the rest of the scale; the legend says so.
  const { lo, hi } = useMemo(() => {
    const vals = []
    if (snap?.zones) {
      for (const col of Object.values(snap.zones)) {
        for (const v of col) if (v != null) vals.push(v)
      }
    }
    for (const z of rows || []) if (z.price_close != null) vals.push(z.price_close)
    if (!vals.length) return { lo: 0, hi: 1 }
    vals.sort((a, b) => a - b)
    const p2 = percentile(vals, 0.02)
    const p95 = percentile(vals, 0.95)
    return { lo: Math.min(p2, 0), hi: Math.max(p95, 1) }
  }, [snap, rows])

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

  const layers = useMemo(() => {
    if (!geo) return []
    const zoneFill = (zone) => {
      const z = byZone.get(zone)
      if (!z) return pal.contextFill
      if (fill === 'state') return [...(pal.state[z.state] || pal.mid), 215]
      return [...priceColor(z.price_close, lo, hi, pal), 235]
    }
    const arcLayer = overlays.flows && atLatest && arcs.length > 0
      ? makeFlowArcsLayer({ arcs, pal, onBorderSelect })
      : null
    const zonesLayer = makeZonesLayer({
      geo, zoneFill, pal, theme, fill, effRows, lo, hi, selectedZone, onZoneClick: onZoneSelect,
    })
    if (view === 'points') {
      const pointFill = (p) => {
        if (fill === 'state') return [...(pal.state[p.state] || pal.mid), 240]
        return [...priceColor(p.price, lo, hi, pal), 240]
      }
      return [
        makeContextZonesLayer(zonesLayer, { pal, theme, selectedZone }),
        ...(arcLayer ? [arcLayer] : []),
        makePointsLayer({ points, pointFill, pal, theme, fill, effRows, lo, hi, onZoneClick: onZoneSelect }),
      ]
    }
    const labels = makeLabelsLayer({ points, pal, theme, effRows })
    const base = fill === 'price' ? [zonesLayer, labels] : [zonesLayer]
    return arcLayer ? [...base, arcLayer] : base
  }, [geo, view, fill, effRows, byZone, lo, hi, points, theme, arcs, overlays.flows, atLatest, onBorderSelect, onZoneSelect, selectedZone, pal])

  const getTooltip = useMemo(() => makeTooltip(byZone, pal), [byZone, pal])

  const zoneCount = byZone.size

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[12px] font-semibold text-neutral-300">Europe · power map</span>
          <InfoPopover text="Real bidding-zone geometry (SE1–SE4, NO1–NO5, Italian sub-zones), shaded by the day-ahead price — or by grid state. IMPORTANT: it shades ONE HOUR at a time (the hour on the slider below), not the whole day — so a zone can read €0 here at 08:00 while the all-zones table shows a positive daily mean. Drag the slider to move through the hours. Fixed colour scale across the shown week: violet = negative prices (a distinct state, not just cheap), brighter cyan = more expensive. Dark shapes = neighbouring countries, no data by design. FLOWS arcs = the latest cross-border flow per border: the faint end exports, the solid end imports; width ∝ GW, colour = how loaded the border is vs its offered day-ahead capacity (grey = no NTC published or no reading); they always show the latest hour and hide while you scrub the past — click one for the border detail below. Zone geometry © Electricity Maps contributors (AGPL). Data: ENTSO-E. Descriptive, not a forecast." />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            {[['zones', 'ZONES'], ['points', 'POINTS']].map(([v, l]) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
                  view === v ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'
                }`}
              >
                {l}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            {FILLS.map((m) => (
              <button
                key={m.key}
                onClick={() => setFill(m.key)}
                className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
                  fill === m.key
                    ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10'
                    : 'text-neutral-500 border-border hover:text-neutral-300'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setOverlays((o) => ({ ...o, flows: !o.flows }))}
            className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
              overlays.flows ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'
            }`}
            title="Cross-border flow arcs (latest hour)"
          >
            FLOWS
          </button>
        </div>
      </div>

      <div
        className={`relative ${tall ? 'h-[60vh] lg:h-[min(75vh,calc(100vh-230px))]' : ''}`}
        style={tall ? { background: pal.surface } : { height: 460, background: pal.surface }}
      >
        <DeckGL initialViewState={INITIAL_VIEW} controller={true} layers={layers} getTooltip={getTooltip} />
      </div>

      {fillDef.scrub && ts.length > 1 && <Scrubber ts={ts} effIdx={effIdx} setIdx={setIdx} />}

      {overlays.flows && <FlowArcLegend pal={pal} atLatest={atLatest} />}

      <div className="flex items-center justify-between gap-2 px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-600">
        {fill === 'price' ? <PriceScaleLegend lo={lo} hi={hi} pal={pal} /> : <StateLegend pal={pal} />}
        <span>{zoneCount} zones · ENTSO-E · zones © Electricity Maps</span>
      </div>
    </div>
  )
}
