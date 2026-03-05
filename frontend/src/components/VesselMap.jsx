import { useState, useEffect, useCallback } from 'react'
import { Map } from 'react-map-gl/maplibre'
import DeckGL from '@deck.gl/react'
import { ScatterplotLayer, PolygonLayer } from '@deck.gl/layers'
import 'maplibre-gl/dist/maplibre-gl.css'

const API = '/api'
const POLL_INTERVAL = 30_000

const INITIAL_VIEW = {
  longitude: 45,
  latitude: 20,
  zoom: 2.2,
  pitch: 0,
  bearing: 0,
}

const DARK_MAP_STYLE = {
  version: 8,
  name: 'obsyd-dark',
  sources: {
    'osm-tiles': {
      type: 'raster',
      tiles: ['https://basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png'],
      tileSize: 256,
      attribution: '&copy; CARTO &copy; OpenStreetMap',
    },
  },
  layers: [
    {
      id: 'osm-tiles',
      type: 'raster',
      source: 'osm-tiles',
      minzoom: 0,
      maxzoom: 19,
    },
  ],
}

function zoneToPoly(bounds) {
  const [sw, ne] = bounds
  return [
    [sw[1], sw[0]],
    [ne[1], sw[0]],
    [ne[1], ne[0]],
    [sw[1], ne[0]],
    [sw[1], sw[0]],
  ]
}

export default function VesselMap({ zones }) {
  const [vessels, setVessels] = useState([])
  const [globalVessels, setGlobalVessels] = useState([])
  const [mode, setMode] = useState('geofence') // 'geofence' | 'global'
  const [showThermal, setShowThermal] = useState(false)
  const [thermalData, setThermalData] = useState([])
  const [portwatch, setPortwatch] = useState(null)
  const [marine, setMarine] = useState({})
  const [hurricanes, setHurricanes] = useState([])

  const fetchVessels = useCallback(async () => {
    try {
      if (mode === 'global') {
        const res = await fetch(`${API}/vessels/global?limit=5000`)
        if (res.ok) {
          const data = await res.json()
          setGlobalVessels(data)
        }
      }
      // Always fetch zone vessels for zone cards
      const res = await fetch(`${API}/vessels/positions?limit=2000`)
      if (res.ok) {
        const data = await res.json()
        setVessels(data)
      }
    } catch {
      // silent fail, will retry
    }
  }, [mode])

  useEffect(() => {
    fetch(`${API}/ports/summary`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setPortwatch(d) })
      .catch(() => {})

    fetch(`${API}/weather/marine`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.zones) setMarine(d.zones) })
      .catch(() => {})

    fetch(`${API}/weather/alerts`)
      .then((r) => r.ok ? r.json() : [])
      .then((data) => {
        const withCoords = data.filter((a) => a.latitude && a.longitude)
        setHurricanes(withCoords)
      })
      .catch(() => {})

    fetch(`${API}/thermal/hotspots`)
      .then((r) => r.ok ? r.json() : [])
      .then(setThermalData)
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchVessels()
    const id = setInterval(fetchVessels, POLL_INTERVAL)
    return () => clearInterval(id)
  }, [fetchVessels])

  const isGlobal = mode === 'global'

  const layers = [
    new PolygonLayer({
      id: 'geofences',
      data: zones,
      getPolygon: (z) => zoneToPoly(z.bounds),
      getFillColor: [0, 229, 255, 18],
      getLineColor: [0, 229, 255, 80],
      getLineWidth: 1,
      lineWidthUnits: 'pixels',
      filled: true,
      stroked: true,
      pickable: true,
    }),
    // Global vessels layer (grey, small dots)
    ...(isGlobal
      ? [
          new ScatterplotLayer({
            id: 'global-vessels',
            data: globalVessels.filter((d) => !d.is_tanker || !d.zone),
            getPosition: (d) => [d.lon, d.lat],
            getRadius: 2,
            radiusUnits: 'pixels',
            radiusMinPixels: 1,
            radiusMaxPixels: 4,
            getFillColor: [120, 120, 140, 140],
            pickable: true,
            updateTriggers: { getPosition: [globalVessels.length] },
          }),
          new ScatterplotLayer({
            id: 'global-tankers',
            data: globalVessels.filter((d) => d.is_tanker && !d.zone),
            getPosition: (d) => [d.lon, d.lat],
            getRadius: 3,
            radiusUnits: 'pixels',
            radiusMinPixels: 2,
            radiusMaxPixels: 5,
            getFillColor: [0, 229, 255, 100],
            pickable: true,
            updateTriggers: { getPosition: [globalVessels.length] },
          }),
        ]
      : []),
    // Zone tankers layer (bright, larger dots) — always shown
    new ScatterplotLayer({
      id: 'vessels',
      data: isGlobal
        ? globalVessels.filter((d) => d.is_tanker && d.zone)
        : vessels,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: isGlobal ? 5 : 4,
      radiusUnits: 'pixels',
      radiusMinPixels: 3,
      radiusMaxPixels: 8,
      getFillColor: (d) => d.sog < 0.5 ? [255, 80, 80, 220] : [0, 229, 255, 220],
      pickable: true,
      updateTriggers: {
        getFillColor: [vessels.length, globalVessels.length],
      },
    }),
    // Thermal hotspots layer (orange/yellow, size by brightness)
    ...(showThermal && thermalData.length > 0
      ? [
          new ScatterplotLayer({
            id: 'thermal',
            data: thermalData,
            getPosition: (d) => [d.lon, d.lat],
            getRadius: (d) => Math.max(3, Math.min(10, (d.brightness - 300) / 20)),
            radiusUnits: 'pixels',
            radiusMinPixels: 3,
            radiusMaxPixels: 12,
            getFillColor: (d) => {
              const t = Math.min(1, Math.max(0, (d.brightness - 300) / 100))
              return [255, Math.floor(200 - t * 120), Math.floor(50 - t * 50), 180]
            },
            pickable: true,
            updateTriggers: {
              getRadius: [thermalData.length],
              getFillColor: [thermalData.length],
            },
          }),
        ]
      : []),
    new ScatterplotLayer({
      id: 'hurricanes',
      data: hurricanes,
      getPosition: (d) => [d.longitude, d.latitude],
      getRadius: 18,
      radiusUnits: 'pixels',
      radiusMinPixels: 12,
      radiusMaxPixels: 30,
      getFillColor: [255, 160, 0, 60],
      getLineColor: [255, 160, 0, 200],
      lineWidthUnits: 'pixels',
      getLineWidth: 2,
      stroked: true,
      filled: true,
      pickable: true,
    }),
  ]

  const getTooltip = ({ object, layer }) => {
    if (!object) return null
    if (layer.id === 'vessels' || layer.id === 'global-tankers') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#c8c8d0">
          <div style="color:#00e5ff;font-weight:bold">${object.ship_name || 'UNKNOWN'}</div>
          <div>MMSI: ${object.mmsi}</div>
          <div>SOG: ${object.sog.toFixed(1)} kn</div>
          <div>COG: ${object.cog.toFixed(1)}</div>
          <div>Zone: ${object.zone || 'none'}</div>
        </div>`,
        style: { background: '#0a0a0f', border: '1px solid #1e1e2e', borderRadius: '4px', padding: '8px' },
      }
    }
    if (layer.id === 'global-vessels') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#c8c8d0">
          <div style="color:#787890;font-weight:bold">${object.ship_name || 'UNKNOWN'}</div>
          <div>MMSI: ${object.mmsi} | Type: ${object.ship_type}</div>
          <div>SOG: ${object.sog.toFixed(1)} kn</div>
        </div>`,
        style: { background: '#0a0a0f', border: '1px solid #1e1e2e', borderRadius: '4px', padding: '8px' },
      }
    }
    if (layer.id === 'thermal') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#ffa000">
          <div style="font-weight:bold">THERMAL HOTSPOT</div>
          <div style="color:#c8c8d0">Brightness: ${object.brightness.toFixed(1)} K</div>
          <div style="color:#c8c8d0">Confidence: ${object.confidence}</div>
          <div style="color:#c8c8d0">Area: ${object.area_name}</div>
          <div style="color:#c8c8d0">${object.acq_date} ${object.acq_time}</div>
        </div>`,
        style: { background: '#0a0a0f', border: '1px solid #ffa000', borderRadius: '4px', padding: '8px' },
      }
    }
    if (layer.id === 'geofences') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#00e5ff">${object.display_name}</div>`,
        style: { background: '#0a0a0f', border: '1px solid #1e1e2e', borderRadius: '4px', padding: '6px' },
      }
    }
    if (layer.id === 'hurricanes') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#ffa000;font-weight:bold">${object.event}<br/><span style="color:#c8c8d0;font-weight:normal">${object.area?.substring(0, 80) || ''}</span></div>`,
        style: { background: '#0a0a0f', border: '1px solid #ffa000', borderRadius: '4px', padding: '8px' },
      }
    }
    return null
  }

  const vesselCount = vessels.length
  const globalCount = globalVessels.length

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <span className="font-mono text-xs text-neutral-500">
          AIS VESSEL MAP // {isGlobal ? 'GLOBAL VIEW' : 'GEOFENCE MONITORING'}
        </span>
        <div className="flex items-center gap-4 font-mono text-[10px]">
          {/* Toggle */}
          <div className="flex items-center border border-border rounded overflow-hidden">
            <button
              onClick={() => setMode('geofence')}
              className={`px-2 py-0.5 transition-colors ${
                !isGlobal
                  ? 'bg-cyan-glow/15 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              GEOFENCE
            </button>
            <button
              onClick={() => setMode('global')}
              className={`px-2 py-0.5 transition-colors ${
                isGlobal
                  ? 'bg-cyan-glow/15 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              ALL VESSELS
            </button>
          </div>
          {/* Thermal toggle */}
          <button
            onClick={() => setShowThermal((v) => !v)}
            className={`px-2 py-0.5 border rounded transition-colors ${
              showThermal
                ? 'bg-orange-400/15 text-orange-400 border-orange-500/30'
                : 'text-neutral-600 border-border hover:text-neutral-400'
            }`}
          >
            THERMAL
          </button>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-cyan-glow" />
            <span className="text-neutral-400">
              {isGlobal ? `${globalCount} vessels` : `${vesselCount} tankers`}
            </span>
          </span>
          {!isGlobal && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-red-400" />
              <span className="text-neutral-400">SOG &lt; 0.5 kn</span>
            </span>
          )}
          {isGlobal && (
            <>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-neutral-500" />
                <span className="text-neutral-500">non-tanker</span>
              </span>
            </>
          )}
          {showThermal && thermalData.length > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-orange-400" />
              <span className="text-orange-400">{thermalData.length} hotspots</span>
            </span>
          )}
          {hurricanes.length > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-orange-400" />
              <span className="text-orange-400">{hurricanes.length} storm alert{hurricanes.length > 1 ? 's' : ''}</span>
            </span>
          )}
          <span className="text-neutral-600">30s poll</span>
        </div>
      </div>

      <div className="relative h-[450px] w-full">
        <DeckGL
          initialViewState={INITIAL_VIEW}
          controller={true}
          layers={layers}
          getTooltip={getTooltip}
        >
          <Map mapStyle={DARK_MAP_STYLE} />
        </DeckGL>
      </div>

      <div className="px-4 py-3 border-t border-border">
        <div className="font-mono text-[10px] text-neutral-600 mb-2 tracking-wider">
          ACTIVE GEOFENCE ZONES
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          {zones.map((z) => {
            const count = vessels.filter((v) => v.zone === z.name).length
            const cp = portwatch?.chokepoints?.find((c) => c.zone === z.name)
            return (
              <div
                key={z.name}
                className="border border-border bg-surface-light rounded px-3 py-2 group hover:border-cyan-glow/30 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-cyan-glow">
                    {z.name.toUpperCase()}
                  </span>
                  {z.no_ais_coverage ? (
                    <span className="font-mono text-[9px] text-neutral-600">NO AIS</span>
                  ) : count > 0 ? (
                    <span className="font-mono text-[10px] text-green-glow">{count}</span>
                  ) : null}
                </div>
                <div className="font-mono text-[10px] text-neutral-600 mt-0.5 leading-tight">
                  {z.display_name}
                </div>
                {(cp || marine[z.name]) && (
                  <div className="mt-1.5 pt-1.5 border-t border-border">
                    {cp && (
                      <div className="font-mono text-[10px] text-neutral-500">
                        {cp.vessel_count} transits
                        <span className="text-neutral-600"> / </span>
                        {cp.vessel_count_tanker} tanker
                      </div>
                    )}
                    {marine[z.name] && (
                      <div className="font-mono text-[10px] text-neutral-600 mt-0.5">
                        {marine[z.name].wind_speed != null && (
                          <span>{marine[z.name].wind_speed.toFixed(0)} kn </span>
                        )}
                        {marine[z.name].wave_height != null && (
                          <span>{marine[z.name].wave_height.toFixed(1)}m</span>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
        {portwatch && (
          <div className="font-mono text-[9px] text-neutral-700 mt-2">
            Source: IMF PortWatch{portwatch.date ? ` (${portwatch.date})` : ''}
          </div>
        )}
      </div>
    </div>
  )
}
