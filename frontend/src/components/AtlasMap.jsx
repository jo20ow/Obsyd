import { useState, useEffect, useMemo } from 'react'
import { Map as MapGL } from 'react-map-gl/maplibre'
import DeckGL from '@deck.gl/react'
import { _GlobeView as GlobeView } from '@deck.gl/core'
import { GeoJsonLayer, SolidPolygonLayer } from '@deck.gl/layers'
import 'maplibre-gl/dist/maplibre-gl.css'
import { InfoPopover } from './Panel'
import { rampColor, NO_DATA_COLOR } from '../utils/chart'

const API = '/api'

const INITIAL_VIEW_2D = { longitude: 10, latitude: 25, zoom: 1.2, pitch: 0, bearing: 0 }
const INITIAL_VIEW_GLOBE = { longitude: 10, latitude: 25, zoom: 0, pitch: 0, bearing: 0 }

// Ocean sphere for the globe: two hemisphere quads (each spans 180° lon, with intermediate
// vertices so they tessellate smoothly onto the sphere). The raster basemap can't render
// under _GlobeView, so this is the globe background.
function _hemi(lonStart, lonEnd) {
  const pts = []
  const steps = 8
  for (let i = 0; i <= steps; i++) pts.push([lonStart + ((lonEnd - lonStart) * i) / steps, -89])
  for (let i = 0; i <= steps; i++) pts.push([lonEnd + ((lonStart - lonEnd) * i) / steps, 89])
  return pts
}
const OCEAN = [{ polygon: _hemi(-180, 0) }, { polygon: _hemi(0, 180) }]

const DARK_MAP_STYLE = {
  version: 8,
  name: 'obsyd-dark',
  sources: {
    'carto-dark': {
      type: 'raster',
      tiles: ['https://basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png'],
      tileSize: 256,
      attribution: '&copy; CARTO &copy; OpenStreetMap',
    },
  },
  layers: [{ id: 'carto-dark', type: 'raster', source: 'carto-dark', minzoom: 0, maxzoom: 19 }],
}

// Curated metric catalog. Each maps to one of the /api/atlas/{energy,macro,resources}
// endpoints (all return `{countries:[{iso3,value,unit?}], as_of, coverage, source}`).
const METRICS = [
  { group: 'Macro', key: 'gdp_usd', label: 'GDP (US$)', kind: 'macro', q: 'metric=gdp_usd' },
  { group: 'Macro', key: 'gdp_per_capita', label: 'GDP per capita', kind: 'macro', q: 'metric=gdp_per_capita' },
  { group: 'Macro', key: 'population', label: 'Population', kind: 'macro', q: 'metric=population' },
  { group: 'Macro', key: 'gdp_growth', label: 'GDP growth %', kind: 'macro', q: 'metric=gdp_growth' },
  { group: 'Macro', key: 'inflation', label: 'Inflation %', kind: 'macro', q: 'metric=inflation' },
  { group: 'Macro', key: 'trade_pct_gdp', label: 'Trade % GDP', kind: 'macro', q: 'metric=trade_pct_gdp' },
  { group: 'Macro', key: 'industry_pct_gdp', label: 'Industry % GDP', kind: 'macro', q: 'metric=industry_pct_gdp' },
  { group: 'Macro', key: 'unemployment_pct', label: 'Unemployment %', kind: 'macro', q: 'metric=unemployment_pct' },
  { group: 'Climate', key: 'co2_per_capita', label: 'CO₂ per capita', kind: 'macro', q: 'metric=co2_per_capita' },
  { group: 'Climate', key: 'renewable_energy_pct', label: 'Renewable energy %', kind: 'macro', q: 'metric=renewable_energy_pct' },
  { group: 'Climate', key: 'co2_emissions', label: 'CO₂ emissions', kind: 'energy', q: 'product=co2_emissions&activity=emissions' },
  { group: 'Energy', key: 'oil_prod', label: 'Oil production', kind: 'energy', q: 'product=petroleum&activity=production' },
  { group: 'Energy', key: 'gas_prod', label: 'Gas production', kind: 'energy', q: 'product=natural_gas&activity=production' },
  { group: 'Energy', key: 'elec_gen', label: 'Electricity generation', kind: 'energy', q: 'product=electricity&activity=generation' },
  { group: 'Energy', key: 'nuclear', label: 'Nuclear energy', kind: 'energy', q: 'product=nuclear&activity=production' },
  { group: 'Energy', key: 'renewables', label: 'Renewable energy', kind: 'energy', q: 'product=renewables&activity=production' },
  { group: 'Resources', key: 'lithium', label: 'Lithium', kind: 'resources', q: 'commodity=lithium' },
  { group: 'Resources', key: 'rare_earths', label: 'Rare earths', kind: 'resources', q: 'commodity=rare_earths' },
  { group: 'Resources', key: 'cobalt', label: 'Cobalt', kind: 'resources', q: 'commodity=cobalt' },
  { group: 'Resources', key: 'copper', label: 'Copper', kind: 'resources', q: 'commodity=copper' },
  { group: 'Resources', key: 'gold', label: 'Gold', kind: 'resources', q: 'commodity=gold' },
  { group: 'Resources', key: 'iron_ore', label: 'Iron ore', kind: 'resources', q: 'commodity=iron_ore' },
]

const GROUPS = ['Macro', 'Climate', 'Energy', 'Resources']

function fmtVal(v, unit) {
  if (v == null || !Number.isFinite(v)) return '—'
  const a = Math.abs(v)
  let s
  if (a >= 1e12) s = (v / 1e12).toFixed(1) + 'T'
  else if (a >= 1e9) s = (v / 1e9).toFixed(1) + 'B'
  else if (a >= 1e6) s = (v / 1e6).toFixed(1) + 'M'
  else if (a >= 1e3) s = (v / 1e3).toFixed(1) + 'k'
  else s = a < 10 ? v.toFixed(1) : Math.round(v).toString()
  return unit ? `${s} ${unit}` : s
}

export default function AtlasMap() {
  const [geo, setGeo] = useState(null)
  const [metricKey, setMetricKey] = useState('gdp_usd')
  const [resp, setResp] = useState(null)
  const [globe, setGlobe] = useState(false)
  const [viewState, setViewState] = useState(INITIAL_VIEW_2D)

  const toggleGlobe = () => {
    setGlobe((g) => {
      setViewState(g ? INITIAL_VIEW_2D : INITIAL_VIEW_GLOBE)
      return !g
    })
  }

  const metric = METRICS.find((m) => m.key === metricKey) || METRICS[0]

  useEffect(() => {
    fetch('/geo/world-110m.geojson')
      .then((r) => r.json())
      .then(setGeo)
      .catch((e) => console.error('AtlasMap geojson:', e))
  }, [])

  useEffect(() => {
    let alive = true
    fetch(`${API}/atlas/${metric.kind}?${metric.q}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setResp(d) })
      .catch((e) => console.error('AtlasMap data:', e))
    return () => { alive = false }
  }, [metric.kind, metric.q])

  // value-by-iso3 + percentile rank (robust to power-law distributions like GDP/population).
  const { valueByIso, rankByIso, unit } = useMemo(() => {
    const countries = resp?.countries || []
    const vmap = new Map()
    for (const c of countries) if (Number.isFinite(c.value)) vmap.set(c.iso3, c.value)
    const sorted = [...vmap.values()].sort((a, b) => a - b)
    const rmap = new Map()
    const n = sorted.length
    for (const [iso, v] of vmap) {
      const idx = sorted.indexOf(v)
      rmap.set(iso, n <= 1 ? 1 : idx / (n - 1))
    }
    return { valueByIso: vmap, rankByIso: rmap, unit: countries[0]?.unit || '' }
  }, [resp])

  const layers = useMemo(() => {
    if (!geo) return []
    const ocean = globe
      ? [new SolidPolygonLayer({ id: 'ocean', data: OCEAN, getPolygon: (d) => d.polygon, getFillColor: [11, 15, 26], stroked: false })]
      : []
    return [
      ...ocean,
      new GeoJsonLayer({
        id: 'atlas-choropleth',
        data: geo,
        pickable: true,
        stroked: true,
        filled: true,
        getFillColor: (f) => {
          const iso = f.properties.iso3
          if (!rankByIso.has(iso)) return [...NO_DATA_COLOR, 90]
          return [...rampColor(rankByIso.get(iso)), 205]
        },
        getLineColor: [70, 75, 95, 180],
        lineWidthMinPixels: 0.5,
        updateTriggers: { getFillColor: [metricKey, resp] },
      }),
    ]
  }, [geo, rankByIso, metricKey, resp, globe])

  const views = useMemo(() => (globe ? [new GlobeView({ id: 'globe', controller: true })] : undefined), [globe])

  const getTooltip = ({ object }) => {
    if (!object) return null
    const iso = object.properties.iso3
    const v = valueByIso.get(iso)
    return {
      text: `${object.properties.name}\n${metric.label}: ${v == null ? 'no data' : fmtVal(v, unit)}`,
      style: { background: '#0a0a12', border: '1px solid #2a2a3a', color: '#d4d4d8', fontFamily: 'monospace', fontSize: '11px', padding: '6px 8px' },
    }
  }

  const top = (resp?.countries || []).slice(0, 5)

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-xs text-neutral-500 tracking-wider">ATLAS · WORLD</span>
          <InfoPopover text="Per-country choropleth over free, official, redistributable data (EIA International / World Bank CC BY 4.0 / USGS — all public domain or CC BY). Countries are shaded by their rank for the selected metric; grey = no data (not zero). Annual figures, lagging — see 'as of'. Descriptive, not a forecast." />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleGlobe}
            className="font-mono text-[10px] tracking-wider border border-border rounded px-2 py-1 text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
            title={globe ? 'Switch to flat map' : 'Switch to globe'}
          >
            {globe ? '◐ GLOBE' : '▦ 2D'}
          </button>
          <select
            value={metricKey}
            onChange={(e) => setMetricKey(e.target.value)}
            className="bg-[#0a0a12] border border-border rounded font-mono text-[11px] text-neutral-300 px-2 py-1 focus:outline-none focus:border-cyan-glow/40"
          >
            {GROUPS.map((g) => (
              <optgroup key={g} label={g}>
                {METRICS.filter((m) => m.group === g).map((m) => (
                  <option key={m.key} value={m.key}>{m.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
      </div>

      <div className="relative h-[520px] w-full">
        <DeckGL
          views={views}
          viewState={viewState}
          onViewStateChange={({ viewState: vs }) => setViewState(vs)}
          controller={true}
          layers={layers}
          getTooltip={getTooltip}
          onError={(e) => console.error('DeckGL error:', e)}
        >
          {!globe && <MapGL mapStyle={DARK_MAP_STYLE} />}
        </DeckGL>

        {/* Legend + ranking overlay */}
        <div className="absolute bottom-3 left-3 bg-[#0a0a0f]/90 border border-border rounded font-mono text-[10px] px-3 py-2.5 max-w-[240px]">
          <div className="text-neutral-300 mb-1.5">{metric.label}{!resp && <span className="text-neutral-600 ml-1">…</span>}</div>
          <div className="h-2 rounded mb-1" style={{ background: 'linear-gradient(90deg, rgb(18,22,38), rgb(20,92,120), rgb(34,185,205), rgb(240,222,96))' }} />
          <div className="flex justify-between text-neutral-600 mb-2"><span>low</span><span>high</span></div>
          {top.length > 0 && (
            <div className="space-y-0.5 mb-2">
              {top.map((c) => (
                <div key={c.iso3} className="flex justify-between gap-2">
                  <span className="text-neutral-400">{c.iso3} {c.country_name?.slice(0, 14)}</span>
                  <span className="text-cyan-glow">{fmtVal(c.value, '')}</span>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-1.5 text-neutral-600">
            <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: `rgb(${NO_DATA_COLOR.join(',')})` }} /> no data
          </div>
          <div className="text-neutral-700 mt-1.5 leading-snug">
            {resp?.coverage ?? 0} countries · as of {resp?.as_of ?? '—'}<br />{resp?.source}
          </div>
        </div>
      </div>
    </div>
  )
}
