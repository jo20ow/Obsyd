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
  const [vesselCount, setVesselCount] = useState(0)
  const [portwatch, setPortwatch] = useState(null)
  const [marine, setMarine] = useState({})
  const [hurricanes, setHurricanes] = useState([])

  const fetchVessels = useCallback(async () => {
    try {
      const res = await fetch(`${API}/vessels/positions?limit=2000`)
      if (res.ok) {
        const data = await res.json()
        setVessels(data)
        setVesselCount(data.length)
      }
    } catch {
      // silent fail, will retry
    }
  }, [])

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
  }, [])

  useEffect(() => {
    fetchVessels()
    const id = setInterval(fetchVessels, POLL_INTERVAL)
    return () => clearInterval(id)
  }, [fetchVessels])

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
    new ScatterplotLayer({
      id: 'vessels',
      data: vessels,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: 4,
      radiusUnits: 'pixels',
      radiusMinPixels: 3,
      radiusMaxPixels: 8,
      getFillColor: (d) => d.sog < 0.5 ? [255, 80, 80, 220] : [0, 229, 255, 220],
      pickable: true,
      updateTriggers: {
        getFillColor: [vessels.length],
      },
    }),
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
    if (layer.id === 'vessels') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#c8c8d0">
          <div style="color:#00e5ff;font-weight:bold">${object.ship_name || 'UNKNOWN'}</div>
          <div>MMSI: ${object.mmsi}</div>
          <div>SOG: ${object.sog.toFixed(1)} kn</div>
          <div>COG: ${object.cog.toFixed(1)}</div>
          <div>Zone: ${object.zone}</div>
        </div>`,
        style: { background: '#0a0a0f', border: '1px solid #1e1e2e', borderRadius: '4px', padding: '8px' },
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

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <span className="font-mono text-xs text-neutral-500">
          AIS VESSEL MAP // GEOFENCE MONITORING
        </span>
        <div className="flex items-center gap-4 font-mono text-[10px]">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-cyan-glow" />
            <span className="text-neutral-400">{vesselCount} tankers</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-red-400" />
            <span className="text-neutral-400">SOG &lt; 0.5 kn</span>
          </span>
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
                  {count > 0 && (
                    <span className="font-mono text-[10px] text-green-glow">{count}</span>
                  )}
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
