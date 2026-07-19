import { useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { GeoJsonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import { InfoPopover } from './Panel'
import { useTheme } from '../context/ThemeContext'

const API = '/api'

// Approximate [lon, lat] centroid per bidding zone for the POINTS view. Europe is a
// ZONAL market — one price per bidding zone, not nodal like the US — so one dot per
// zone is the honest granularity. IT_CALABRIA has no polygon in the zone geometry
// (it is part of IT-SO there) and appears on the map only as a point.
const ZONE_COORDS = {
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

// ── Theme-aware palettes ──────────────────────────────────────────────────────
// The map lives inside a themed panel; a hard-coded dark map inside the light
// desk was exactly what read as cheap. Price is a DIVERGING scale around
// 0 €/MWh (negative prices are a distinct market state, not just "cheap"):
// both poles start at a neutral midpoint and gain chroma/contrast toward their
// hue — on the dark surface they brighten, on the light surface they darken,
// so "far from zero" always means "more contrast vs the surface". Poles and
// status trios are validator-checked per surface (dark: cyan/violet ΔE 12.6;
// light: teal/violet ΔE 55.9; the light amber's 2.69:1 contrast WARN is
// relieved by the worded legend + the overview table beside the map — a darker
// amber collapses into red for deuteranopia at ΔE 3.8, so it stays).
const PALETTES = {
  dark: {
    surface: '#06060a',
    mid: [26, 26, 36],
    posPole: [103, 232, 249], // cyan-300: expensive = bright
    negPole: [196, 181, 253], // violet-300: negative prices
    contextFill: [10, 10, 16, 255],
    contextLine: [42, 42, 58, 160],
    zoneLine: [6, 6, 10, 220],
    state: { CALM: [74, 222, 128], ELEVATED: [250, 204, 21], STRESSED: [248, 113, 113] },
    stateLegend: { CALM: '#4ade80', ELEVATED: '#facc15', STRESSED: '#f87171' },
    label: [235, 240, 245, 230],
    labelOutline: [6, 6, 10, 255],
    highlight: [103, 232, 249, 60],
    tooltip: { background: '#0a0a12', border: '1px solid #2a2a3a', color: '#d4d4d8' },
  },
  light: {
    surface: '#f4f5f7',
    mid: [226, 230, 235],
    posPole: [8, 100, 124],   // teal-900: expensive = dark/saturated
    negPole: [109, 40, 217],  // violet-700
    contextFill: [229, 231, 236, 255],
    contextLine: [203, 208, 216, 200],
    zoneLine: [255, 255, 255, 235],
    state: { CALM: [22, 163, 74], ELEVATED: [202, 138, 4], STRESSED: [220, 38, 38] },
    stateLegend: { CALM: '#16a34a', ELEVATED: '#ca8a04', STRESSED: '#dc2626' },
    label: [24, 30, 40, 235],
    labelOutline: [255, 255, 255, 255],
    highlight: [8, 100, 124, 50],
    tooltip: { background: '#ffffff', border: '1px solid #d6dae0', color: '#1f2430' },
  },
}

function lerp(a, b, t) {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ]
}

// v → RGB for the diverging price scale on the fixed domain [lo, hi] (lo may be ≥0,
// then only the positive pole is in play). Values are clamped to the domain.
function priceColor(v, lo, hi, pal) {
  if (v == null) return pal.mid
  if (v >= 0) {
    const span = Math.max(hi, 1e-6)
    return lerp(pal.mid, pal.posPole, Math.min(Math.max(v / span, 0), 1))
  }
  const span = Math.max(-lo, 1e-6)
  return lerp(pal.mid, pal.negPole, Math.min(Math.max(-v / span, 0), 1))
}

// The legend is GENERATED from priceColor — it cannot drift from the map.
function legendGradient(lo, hi, pal) {
  const stops = []
  for (let i = 0; i <= 12; i++) {
    const v = lo + ((hi - lo) * i) / 12
    const [r, g, b] = priceColor(v, lo, hi, pal)
    stops.push(`rgb(${r},${g},${b}) ${(i / 12) * 100}%`)
  }
  return `linear-gradient(90deg, ${stops.join(', ')})`
}

function percentile(sorted, p) {
  if (sorted.length === 0) return 0
  const i = (sorted.length - 1) * p
  const f = Math.floor(i)
  return sorted[f] + (sorted[Math.min(f + 1, sorted.length - 1)] - sorted[f]) * (i - f)
}

const INITIAL_VIEW = { longitude: 9, latitude: 54, zoom: 3.1, minZoom: 2.5, maxZoom: 6 }

const METRICS = [
  { key: 'price', label: 'DAY-AHEAD €/MWh' },
  { key: 'state', label: 'GRID STATE' },
]

function fmtTs(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC',
  })
}

export default function PowerMap() {
  const { theme } = useTheme()
  const pal = PALETTES[theme] || PALETTES.dark
  const [geo, setGeo] = useState(null)
  const [rows, setRows] = useState(null)
  const [metric, setMetric] = useState('price')
  const [view, setView] = useState('zones') // 'zones' choropleth | 'points' per-zone dots
  const [snap, setSnap] = useState(null) // hourly day-ahead price matrix (scrubber)
  const [idx, setIdx] = useState(null)   // selected hour index; null = latest/live

  useEffect(() => {
    fetch('/geo/eu-zones.geojson').then((r) => r.json()).then(setGeo).catch((e) => console.error('PowerMap geo:', e))
  }, [])
  useEffect(() => {
    let alive = true
    fetch(`${API}/power/overview`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setRows(d?.zones || []) })
      .catch((e) => console.error('PowerMap overview:', e))
    return () => { alive = false }
  }, [])
  useEffect(() => {
    let alive = true
    fetch(`${API}/v1/snapshot?series=price.dayahead&hours=168`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d?.available) setSnap(d) })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const ts = snap?.timestamps || []
  const effIdx = idx == null ? ts.length - 1 : idx

  // When scrubbing (price metric + snapshot loaded), override each zone's price with
  // the day-ahead price at the selected hour; otherwise use the live overview.
  const effRows = useMemo(() => {
    if (metric !== 'price' || !snap?.zones || ts.length === 0) return rows || []
    return (rows || []).map((z) => {
      const col = snap.zones[z.zone]
      const v = col ? col[effIdx] : undefined
      return v == null ? z : { ...z, price_close: v }
    })
  }, [rows, snap, effIdx, metric, ts.length])

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

  const zoneFill = (zone) => {
    const z = byZone.get(zone)
    if (!z) return pal.contextFill
    if (metric === 'state') return [...(pal.state[z.state] || pal.mid), 215]
    return [...priceColor(z.price_close, lo, hi, pal), 235]
  }

  const layers = useMemo(() => {
    if (!geo) return []
    const zonesLayer = new GeoJsonLayer({
      id: 'eu-zones',
      data: geo,
      pickable: true,
      stroked: true,
      filled: true,
      getFillColor: (f) => (f.properties.zone ? zoneFill(f.properties.zone) : pal.contextFill),
      getLineColor: (f) => (f.properties.zone ? pal.zoneLine : pal.contextLine),
      lineWidthMinPixels: 1,
      autoHighlight: true,
      highlightColor: pal.highlight,
      updateTriggers: { getFillColor: [metric, effRows, lo, hi, theme], getLineColor: [theme] },
    })
    if (view === 'points') {
      const pointFill = (p) => {
        if (metric === 'state') return [...(pal.state[p.state] || pal.mid), 240]
        return [...priceColor(p.price, lo, hi, pal), 240]
      }
      return [
        zonesLayer.clone({ pickable: false, autoHighlight: false, getFillColor: pal.contextFill, updateTriggers: { getFillColor: [theme] } }),
        new ScatterplotLayer({
          id: 'power-points', data: points, pickable: true,
          getPosition: (d) => d.position, getFillColor: pointFill,
          getRadius: 7, radiusUnits: 'pixels', radiusMinPixels: 5, radiusMaxPixels: 11,
          stroked: true, getLineColor: pal.zoneLine, lineWidthMinPixels: 1,
          updateTriggers: { getFillColor: [metric, effRows, lo, hi, theme], getLineColor: [theme] },
        }),
      ]
    }
    const labels = new TextLayer({
      id: 'zone-price-labels',
      data: points.filter((p) => p.price != null),
      getPosition: (d) => d.position,
      getText: (d) => `${Math.round(d.price)}`,
      getColor: pal.label,
      outlineColor: pal.labelOutline,
      outlineWidth: 2,
      fontSettings: { sdf: true },
      fontFamily: 'ui-monospace, Menlo, monospace',
      // Meters, not pixels: labels grow with zoom, so the dense Benelux/Baltic
      // cluster stays quiet at continent zoom and becomes readable on approach.
      sizeUnits: 'meters',
      getSize: 60000,
      sizeMaxPixels: 13,
      billboard: true,
      pickable: false,
      updateTriggers: { getText: [effRows], getPosition: [effRows], getColor: [theme] },
    })
    return metric === 'price' ? [zonesLayer, labels] : [zonesLayer]
  }, [geo, view, metric, effRows, byZone, lo, hi, points, theme])

  const TIP_STYLE = { ...pal.tooltip, fontFamily: 'monospace', fontSize: '11px', padding: '6px 8px' }
  const getTooltip = ({ object }) => {
    if (!object) return null
    if (object.position && object.zone) { // a scatter point
      const price = object.price != null ? `${object.price.toFixed(1)} €/MWh` : 'n/a'
      return { text: `${object.label} · ${object.state || ''}\nDay-ahead: ${price}`, style: TIP_STYLE }
    }
    const zone = object.properties?.zone
    if (!zone) return null // neighbouring country — context only
    const z = byZone.get(zone)
    if (!z) return { text: `${zone}\nno data yet`, style: TIP_STYLE }
    const price = z.price_close != null ? `${z.price_close.toFixed(1)} €/MWh` : 'n/a'
    return {
      text: `${z.zone_label || zone} · ${z.state || ''}\nDay-ahead: ${price}\nResidual z: ${z.residual_z != null ? z.residual_z.toFixed(1) : 'n/a'}`,
      style: TIP_STYLE,
    }
  }

  const zoneCount = byZone.size

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[12px] font-semibold text-neutral-300">Europe · power map</span>
          <InfoPopover text="Real bidding-zone geometry (SE1–SE4, NO1–NO5, Italian sub-zones), shaded by the day-ahead price — or by grid state. IMPORTANT: it shades ONE HOUR at a time (the hour on the slider below), not the whole day — so a zone can read €0 here at 08:00 while the all-zones table shows a positive daily mean. Drag the slider to move through the hours. Fixed colour scale across the shown week: violet = negative prices (a distinct state, not just cheap), brighter cyan = more expensive. Dark shapes = neighbouring countries, no data by design. Zone geometry © Electricity Maps contributors (AGPL). Data: ENTSO-E. Descriptive, not a forecast." />
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
            {METRICS.map((m) => (
              <button
                key={m.key}
                onClick={() => setMetric(m.key)}
                className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
                  metric === m.key
                    ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10'
                    : 'text-neutral-500 border-border hover:text-neutral-300'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="relative" style={{ height: 460, background: pal.surface }}>
        <DeckGL initialViewState={INITIAL_VIEW} controller={true} layers={layers} getTooltip={getTooltip} />
      </div>

      {/* Time scrubber — slide the map through the last 7 days of day-ahead prices. */}
      {metric === 'price' && ts.length > 1 && (
        <div className="flex items-center gap-2 px-4 py-2 border-t border-border">
          <span className="font-mono text-[9px] text-neutral-500 shrink-0 w-32">
            {fmtTs(ts[effIdx])}{effIdx === ts.length - 1 ? ' · LATEST' : ' UTC'}
          </span>
          <input
            type="range"
            min={0}
            max={ts.length - 1}
            value={effIdx}
            onChange={(e) => setIdx(Number(e.target.value))}
            className="flex-1 accent-cyan-500"
            aria-label="Time scrubber"
          />
          {effIdx !== ts.length - 1 && (
            <button onClick={() => setIdx(null)} className="font-mono text-[9px] text-neutral-500 hover:text-cyan-glow shrink-0">↺ live</button>
          )}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-600">
        {metric === 'price' ? (
          <span className="flex items-center gap-1" title="Fixed scale across the shown week (2nd–98th percentile); the tooltip has exact values.">
            <span className="text-neutral-500">{lo < 0 ? `≤${lo.toFixed(0)}` : lo.toFixed(0)}</span>
            <span className="relative inline-block h-2 w-28 rounded overflow-hidden" style={{ background: legendGradient(lo, hi, pal) }}>
              {lo < 0 && (
                <span
                  className="absolute top-0 h-2 w-px bg-neutral-400"
                  style={{ left: `${((0 - lo) / (hi - lo)) * 100}%` }}
                  title="0 €/MWh"
                />
              )}
            </span>
            <span className="text-neutral-500">≥{hi.toFixed(0)} €/MWh</span>
            {lo < 0 && <span className="ml-1 text-violet-300/70">violet = negative</span>}
          </span>
        ) : (
          <span className="flex items-center gap-3">
            <span style={{ color: pal.stateLegend.CALM }}>■ CALM</span>
            <span style={{ color: pal.stateLegend.ELEVATED }}>■ ELEVATED</span>
            <span style={{ color: pal.stateLegend.STRESSED }}>■ STRESSED</span>
          </span>
        )}
        <span>{zoneCount} zones · ENTSO-E · zones © Electricity Maps</span>
      </div>
    </div>
  )
}
