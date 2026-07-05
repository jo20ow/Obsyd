import { useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { InfoPopover } from './Panel'
import { rampColor, NO_DATA_COLOR } from '../utils/chart'

const API = '/api'

// Enabled bidding zones → ISO3 of the country polygon that represents them on the map.
// DE_LU ≈ Germany (Luxembourg is subsumed). Italian sub-zones/DK would map many→one and
// are left out of the map until enabled.
const ZONE_TO_ISO3 = {
  DE_LU: 'DEU', FR: 'FRA', NL: 'NLD', BE: 'BEL', AT: 'AUT',
  ES: 'ESP', PT: 'PRT', PL: 'POL', CZ: 'CZE', HU: 'HUN', RO: 'ROU',
  GR: 'GRC', IE_SEM: 'IRL', BG: 'BGR', HR: 'HRV', SI: 'SVN', SK: 'SVK', FI: 'FIN',
  // Sub-zoned countries: many zones → one country polygon (map shows the country mean).
  IT_NORD: 'ITA', IT_CENTRO_NORD: 'ITA', IT_CENTRO_SUD: 'ITA', IT_SUD: 'ITA',
  IT_SICILIA: 'ITA', IT_SARDEGNA: 'ITA', IT_CALABRIA: 'ITA',
  DK1: 'DNK', DK2: 'DNK',
  CH: 'CHE',
  NO1: 'NOR', NO2: 'NOR', NO3: 'NOR', NO4: 'NOR', NO5: 'NOR',
  SE1: 'SWE', SE2: 'SWE', SE3: 'SWE', SE4: 'SWE',
}

// Approximate [lon, lat] centroid per bidding zone for the POINTS view. Europe is a
// ZONAL market — one price per bidding zone, not nodal like the US — so one dot per
// zone is the honest granularity. Sub-zoned countries (IT/NO/SE/DK) get several dots.
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

const STATE_ORDER = { CALM: 0, ELEVATED: 1, STRESSED: 2 }

const STATE_COLOR = {
  CALM: [74, 222, 128],       // green
  ELEVATED: [250, 204, 21],   // amber
  STRESSED: [248, 113, 113],  // red
}

const INITIAL_VIEW = { longitude: 8, latitude: 49, zoom: 3.3, minZoom: 2.5, maxZoom: 6 }

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
  const [geo, setGeo] = useState(null)
  const [rows, setRows] = useState(null)
  const [metric, setMetric] = useState('price')
  const [view, setView] = useState('countries') // 'countries' choropleth | 'points' per-zone dots
  const [snap, setSnap] = useState(null) // hourly day-ahead price matrix (scrubber)
  const [idx, setIdx] = useState(null)   // selected hour index; null = latest/live

  useEffect(() => {
    fetch('/geo/world-110m.geojson').then((r) => r.json()).then(setGeo).catch((e) => console.error('PowerMap geo:', e))
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

  // Index rows by ISO3 (aggregating sub-zoned countries: mean price, worst state)
  // + normalize price across the covered countries.
  const { byIso, priceMin, priceMax } = useMemo(() => {
    const groups = new Map() // iso -> rows[]
    for (const z of effRows) {
      const iso = ZONE_TO_ISO3[z.zone]
      if (!iso) continue
      if (!groups.has(iso)) groups.set(iso, [])
      groups.get(iso).push(z)
    }
    const m = new Map()
    for (const [iso, zs] of groups) {
      const prices = zs.map((z) => z.price_close).filter((v) => v != null)
      const price = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : null
      const state = zs.map((z) => z.state).filter(Boolean)
        .sort((a, b) => (STATE_ORDER[b] ?? -1) - (STATE_ORDER[a] ?? -1))[0] || null
      const first = zs[0]
      const zoneLabel = zs.length > 1 ? `${first.zone_label || first.zone} +${zs.length - 1}` : (first.zone_label || first.zone)
      m.set(iso, { price_close: price, state, zone_label: zoneLabel, residual_z: first.residual_z })
    }
    const prices = [...m.values()].map((z) => z.price_close).filter((v) => v != null)
    return { byIso: m, priceMin: prices.length ? Math.min(...prices) : 0, priceMax: prices.length ? Math.max(...prices) : 1 }
  }, [effRows])

  // One dot per bidding zone (POINTS view). Normalized over the per-zone prices.
  const { points, ptMin, ptMax } = useMemo(() => {
    const pts = []
    for (const z of effRows) {
      const c = ZONE_COORDS[z.zone]
      if (!c) continue
      pts.push({ position: c, zone: z.zone, label: z.zone_label || z.zone, price: z.price_close, state: z.state })
    }
    const prices = pts.map((p) => p.price).filter((v) => v != null)
    return { points: pts, ptMin: prices.length ? Math.min(...prices) : 0, ptMax: prices.length ? Math.max(...prices) : 1 }
  }, [effRows])

  const layers = useMemo(() => {
    if (!geo) return []
    if (view === 'points') {
      const pointFill = (p) => {
        if (metric === 'state') return [...(STATE_COLOR[p.state] || NO_DATA_COLOR), 235]
        if (p.price == null) return [...NO_DATA_COLOR, 120]
        const t = ptMax > ptMin ? (p.price - ptMin) / (ptMax - ptMin) : 0.5
        return [...rampColor(t), 235]
      }
      return [
        new GeoJsonLayer({
          id: 'power-base', data: geo, filled: true, stroked: true,
          getFillColor: [30, 30, 42, 120], getLineColor: [120, 128, 150, 110], lineWidthMinPixels: 0.5,
        }),
        new ScatterplotLayer({
          id: 'power-points', data: points, pickable: true,
          getPosition: (d) => d.position, getFillColor: pointFill,
          getRadius: 7, radiusUnits: 'pixels', radiusMinPixels: 5, radiusMaxPixels: 11,
          stroked: true, getLineColor: [8, 8, 14, 210], lineWidthMinPixels: 1,
          updateTriggers: { getFillColor: [metric, effRows, ptMin, ptMax] },
        }),
      ]
    }
    const fill = (iso) => {
      const z = byIso.get(iso)
      if (!z) return [...NO_DATA_COLOR, 70]
      if (metric === 'state') return [...(STATE_COLOR[z.state] || NO_DATA_COLOR), 210]
      const p = z.price_close
      if (p == null) return [...NO_DATA_COLOR, 70]
      const t = priceMax > priceMin ? (p - priceMin) / (priceMax - priceMin) : 0.5
      return [...rampColor(t), 210]
    }
    return [
      new GeoJsonLayer({
        id: 'power-choropleth',
        data: geo,
        pickable: true,
        stroked: true,
        filled: true,
        getFillColor: (f) => fill(f.properties.iso3),
        getLineColor: [120, 128, 150, 140],
        lineWidthMinPixels: 0.5,
        updateTriggers: { getFillColor: [metric, effRows] },
      }),
    ]
  }, [geo, view, metric, effRows, byIso, priceMin, priceMax, points, ptMin, ptMax])

  const TIP_STYLE = { background: '#0a0a12', border: '1px solid #2a2a3a', color: '#d4d4d8', fontFamily: 'monospace', fontSize: '11px', padding: '6px 8px' }
  const getTooltip = ({ object }) => {
    if (!object) return null
    if (object.position && object.zone) { // a scatter point
      const price = object.price != null ? `${object.price.toFixed(1)} €/MWh` : 'n/a'
      return { text: `${object.label} · ${object.state || ''}\nDay-ahead: ${price}`, style: TIP_STYLE }
    }
    const z = byIso.get(object.properties?.iso3)
    if (!z) return null
    const price = z.price_close != null ? `${z.price_close.toFixed(1)} €/MWh` : 'n/a'
    return {
      text: `${z.zone_label || z.zone} · ${z.state || ''}\nDay-ahead: ${price}\nResidual z: ${z.residual_z != null ? z.residual_z.toFixed(1) : 'n/a'}`,
      style: TIP_STYLE,
    }
  }

  const covered = byIso.size
  const lo = view === 'points' ? ptMin : priceMin
  const hi = view === 'points' ? ptMax : priceMax
  const count = view === 'points' ? points.length : covered

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[12px] font-semibold text-neutral-300">Europe · power map</span>
          <InfoPopover text="European bidding zones shaded by today's day-ahead price (relative: cheapest→priciest across covered zones) or grid state (CALM/ELEVATED/STRESSED). Grey = zone not enabled. From the official record (ENTSO-E). Descriptive, not a forecast." />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            {[['countries', 'COUNTRIES'], ['points', 'POINTS']].map(([v, l]) => (
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

      <div className="relative" style={{ height: 460, background: '#06060a' }}>
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
          <span className="flex items-center gap-1">
            <span className="text-neutral-500">{lo.toFixed(0)}</span>
            <span className="inline-block h-2 w-24 rounded" style={{ background: 'linear-gradient(90deg,#1e40af,#22d3ee,#facc15,#f87171)' }} />
            <span className="text-neutral-500">{hi.toFixed(0)} €/MWh</span>
          </span>
        ) : (
          <span className="flex items-center gap-3">
            <span style={{ color: '#4ade80' }}>■ CALM</span>
            <span style={{ color: '#facc15' }}>■ ELEVATED</span>
            <span style={{ color: '#f87171' }}>■ STRESSED</span>
          </span>
        )}
        <span>{count} {view === 'points' ? 'zones · dots' : 'zones'} · ENTSO-E</span>
      </div>
    </div>
  )
}
