import { useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { GeoJsonLayer } from '@deck.gl/layers'
import { InfoPopover } from './Panel'
import { rampColor, NO_DATA_COLOR } from '../utils/chart'

const API = '/api'

// Enabled bidding zones → ISO3 of the country polygon that represents them on the map.
// DE_LU ≈ Germany (Luxembourg is subsumed). Italian sub-zones/DK would map many→one and
// are left out of the map until enabled.
const ZONE_TO_ISO3 = {
  DE_LU: 'DEU', FR: 'FRA', NL: 'NLD', BE: 'BEL', AT: 'AUT',
  ES: 'ESP', PT: 'PRT', PL: 'POL', CZ: 'CZE',
}

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

export default function PowerMap() {
  const [geo, setGeo] = useState(null)
  const [rows, setRows] = useState(null)
  const [metric, setMetric] = useState('price')

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

  // Index overview rows by ISO3 + normalize price across the covered zones.
  const { byIso, priceMin, priceMax } = useMemo(() => {
    const m = new Map()
    for (const z of rows || []) {
      const iso = ZONE_TO_ISO3[z.zone]
      if (iso) m.set(iso, z)
    }
    const prices = [...m.values()].map((z) => z.price_close).filter((v) => v != null)
    return { byIso: m, priceMin: prices.length ? Math.min(...prices) : 0, priceMax: prices.length ? Math.max(...prices) : 1 }
  }, [rows])

  const layers = useMemo(() => {
    if (!geo) return []
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
        updateTriggers: { getFillColor: [metric, rows] },
      }),
    ]
  }, [geo, metric, rows, byIso, priceMin, priceMax])

  const getTooltip = ({ object }) => {
    if (!object) return null
    const z = byIso.get(object.properties.iso3)
    if (!z) return null
    const price = z.price_close != null ? `${z.price_close.toFixed(1)} €/MWh` : 'n/a'
    return {
      text: `${z.zone_label || z.zone} · ${z.state || ''}\nDay-ahead: ${price}\nResidual z: ${z.residual_z != null ? z.residual_z.toFixed(1) : 'n/a'}`,
      style: { background: '#0a0a12', border: '1px solid #2a2a3a', color: '#d4d4d8', fontFamily: 'monospace', fontSize: '11px', padding: '6px 8px' },
    }
  }

  const covered = byIso.size

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-xs text-neutral-500 tracking-wider">EUROPE · POWER MAP</span>
          <InfoPopover text="European bidding zones shaded by today's day-ahead price (relative: cheapest→priciest across covered zones) or grid state (CALM/ELEVATED/STRESSED). Grey = zone not enabled. From the official record (ENTSO-E). Descriptive, not a forecast." />
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

      <div className="relative" style={{ height: 460, background: '#06060a' }}>
        <DeckGL initialViewState={INITIAL_VIEW} controller={true} layers={layers} getTooltip={getTooltip} />
      </div>

      <div className="flex items-center justify-between gap-2 px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-600">
        {metric === 'price' ? (
          <span className="flex items-center gap-1">
            <span className="text-neutral-500">{priceMin.toFixed(0)}</span>
            <span className="inline-block h-2 w-24 rounded" style={{ background: 'linear-gradient(90deg,#1e40af,#22d3ee,#facc15,#f87171)' }} />
            <span className="text-neutral-500">{priceMax.toFixed(0)} €/MWh</span>
          </span>
        ) : (
          <span className="flex items-center gap-3">
            <span style={{ color: '#4ade80' }}>■ CALM</span>
            <span style={{ color: '#facc15' }}>■ ELEVATED</span>
            <span style={{ color: '#f87171' }}>■ STRESSED</span>
          </span>
        )}
        <span>{covered} zones · ENTSO-E</span>
      </div>
    </div>
  )
}
